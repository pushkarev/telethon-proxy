from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from collections import deque
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient, events, functions, types, utils
from telethon.sessions import StringSession

from .config import ProxyConfig
from .filtering import FilterResult, ensure_allowed_peer, filter_messages_bundle
from .folders import build_cloud_policy_snapshot
from .hooks import IncomingHook
from .peer_refs import PeerResolver
from .policy import CloudPolicySnapshot
from .update_bus import UpdateBus, UpdateEnvelope

logger = logging.getLogger(__name__)


class UpstreamUnavailableError(RuntimeError):
    pass


class UpstreamAdapter:
    def __init__(
        self,
        config: ProxyConfig,
        *,
        client: TelegramClient | None = None,
        client_factory: Callable[[object, int, str], TelegramClient] | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or (lambda session, api_id, api_hash: TelegramClient(session, api_id, api_hash))
        self.client: TelegramClient | None = client or (
            self._build_client() if self.has_runtime_credentials() and self.has_persisted_session_material() else None
        )
        self.policy = CloudPolicySnapshot(folder_name=config.cloud_folder_name)
        self.update_bus = UpdateBus(buffer_size=config.update_buffer_size)
        self.peer_resolver = PeerResolver()
        self.incoming_hook = IncomingHook(config.incoming_hook_command)
        self._policy_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._recent_update_keys: deque[tuple[str, int | None, int | None]] = deque(maxlen=max(config.update_buffer_size, 100))
        self._recent_update_index: set[tuple[str, int | None, int | None]] = set()
        self._handlers_registered = False
        self._stopping = False

    def has_runtime_credentials(self) -> bool:
        return bool(self.config.upstream_api_id and self.config.upstream_api_hash)

    def has_persisted_session_material(self) -> bool:
        if getattr(self, "client", None) is not None:
            return True
        return self.config.has_upstream_session_material()

    def _build_client(self) -> TelegramClient:
        session = StringSession(self.config.upstream_session_string) if self.config.upstream_session_string else str(
            self.config.upstream_session_path
        )
        return self._client_factory(session, self.config.upstream_api_id, self.config.upstream_api_hash)

    async def start(self) -> None:
        self._stopping = False
        if not self.has_runtime_credentials():
            logger.info("Upstream Telegram credentials are not configured yet; waiting for dashboard authentication")
            return
        if not self.has_persisted_session_material():
            logger.info("Upstream Telegram session is not configured yet; waiting for dashboard authentication")
            return
        self._register_event_handlers()
        self._ensure_supervisor_task()
        try:
            await self.ensure_connected(force_refresh_policy=True)
        except UpstreamUnavailableError as exc:
            logger.warning("Initial upstream connection failed; reconnect supervisor will retry: %s", exc)
        except Exception:
            if self._supervisor_task is not None:
                self._supervisor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._supervisor_task
                self._supervisor_task = None
            raise
        logger.info("Upstream adapter started")

    async def stop(self) -> None:
        self._stopping = True
        if self._refresh_task:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None
        if self._supervisor_task:
            self._supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor_task
            self._supervisor_task = None
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.disconnect()

    async def refresh_policy(self) -> CloudPolicySnapshot:
        await self.ensure_connected()
        async with self._policy_lock:
            return await self._refresh_policy_locked()

    async def ensure_connected(self, *, force_refresh_policy: bool = False) -> None:
        async with self._connect_lock:
            if self._stopping:
                raise UpstreamUnavailableError("Upstream adapter is stopping")
            if not self.has_runtime_credentials():
                raise RuntimeError("Telegram authentication is required in Telegram -> Settings")
            if self.client is None and not self.has_persisted_session_material():
                raise RuntimeError("Telegram authentication is required in Telegram -> Settings")
            if self.client is None:
                self.client = self._build_client()
            refresh_needed = force_refresh_policy or not self.policy.allowed_peers
            if self.client.is_connected():
                if refresh_needed:
                    async with self._policy_lock:
                        await self._refresh_policy_locked()
                return
            try:
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    raise RuntimeError("Telegram authentication is required in Telegram -> Settings")
                if refresh_needed:
                    async with self._policy_lock:
                        await self._refresh_policy_locked()
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                raise UpstreamUnavailableError(str(exc) or exc.__class__.__name__) from exc

    async def get_dialogs(self, limit: int = 100):
        await self.ensure_connected()
        dialogs = await self.client.get_dialogs(limit=None)
        allowed = [dialog for dialog in dialogs if self.policy.allows_peer(dialog.entity)]
        if limit is None:
            return allowed
        return allowed[:limit]

    async def iter_allowed_dialogs(
        self,
        *,
        limit: int | None = None,
        offset_date=None,
        offset_id: int = 0,
        offset_peer=None,
        ignore_pinned: bool = False,
        folder: int | None = None,
        archived: bool | None = None,
    ):
        await self.ensure_connected()
        # The upstream Telethon iterator path is brittle around offset_peer handling
        # in our proxy use case, so we fetch a stable snapshot and apply Cloud filtering
        # before honoring the caller's limit.
        dialogs = await self.client.get_dialogs(
            limit=None,
            ignore_pinned=ignore_pinned,
            folder=folder,
            archived=archived,
        )
        yielded = 0
        for dialog in dialogs:
            if not self.policy.allows_peer(dialog.entity):
                continue
            yield dialog
            yielded += 1
            if limit is not None and yielded >= limit:
                break

    async def resolve_peer(self, peer: Any) -> Any:
        await self.ensure_connected()
        normalized = self.peer_resolver.normalize_peer_ref(peer)
        entity = await self.client.get_input_entity(normalized)
        ensure_allowed_peer(self.policy, entity, action="resolvePeer")
        return entity

    async def resolve_username(self, username: str):
        await self.ensure_connected()
        result = await self.client(functions.contacts.ResolveUsernameRequest(username=username))
        ensure_allowed_peer(self.policy, result.peer, action="resolveUsername")
        return result

    async def get_history(self, peer: Any, limit: int = 100):
        target = await self.resolve_peer(peer)
        result = await self.client(
            functions.messages.GetHistoryRequest(
                peer=target,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
        return filter_messages_bundle(
            policy=self.policy,
            messages=result.messages,
            chats=result.chats,
            users=result.users,
            allow_member_listing=self.config.allow_member_listing,
        )

    async def get_mentions(self, peer: Any, limit: int = 100):
        target = await self.resolve_peer(peer)
        result = await self.client(
            functions.messages.SearchRequest(
                peer=target,
                q="",
                filter=types.InputMessagesFilterMyMentions(),
                min_date=None,
                max_date=None,
                offset_id=0,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0,
                from_id=None,
                saved_peer_id=None,
                saved_reaction=None,
                top_msg_id=None,
            )
        )
        return filter_messages_bundle(
            policy=self.policy,
            messages=result.messages,
            chats=result.chats,
            users=result.users,
            allow_member_listing=self.config.allow_member_listing,
        )

    async def search_messages(
        self,
        peer: Any,
        *,
        query: str,
        filter,
        min_date=None,
        max_date=None,
        offset_id: int = 0,
        add_offset: int = 0,
        limit: int = 100,
        max_id: int = 0,
        min_id: int = 0,
        hash_value: int = 0,
        from_id=None,
        saved_peer_id=None,
        saved_reaction=None,
        top_msg_id: int | None = None,
    ) -> FilterResult:
        target = await self.resolve_peer(peer)
        result = await self.client(
            functions.messages.SearchRequest(
                peer=target,
                q=query,
                filter=filter,
                min_date=min_date,
                max_date=max_date,
                offset_id=offset_id,
                add_offset=add_offset,
                limit=limit,
                max_id=max_id,
                min_id=min_id,
                hash=hash_value,
                from_id=from_id,
                saved_peer_id=saved_peer_id,
                saved_reaction=saved_reaction,
                top_msg_id=top_msg_id,
            )
        )
        return filter_messages_bundle(
            policy=self.policy,
            messages=result.messages,
            chats=result.chats,
            users=result.users,
            allow_member_listing=self.config.allow_member_listing,
        )

    async def search_all_messages(
        self,
        *,
        query: str,
        filter,
        min_date=None,
        max_date=None,
        offset_peer=None,
        offset_id: int = 0,
        limit: int = 100,
        max_id: int = 0,
        min_id: int = 0,
        from_id=None,
        saved_peer_id=None,
        saved_reaction=None,
        top_msg_id: int | None = None,
        broadcasts_only: bool | None = None,
        groups_only: bool | None = None,
        users_only: bool | None = None,
        folder_id: int | None = None,
    ) -> FilterResult:
        await self.ensure_connected()
        if folder_id not in (None, 0):
            return FilterResult(messages=[], chats=[], users=[], dropped_count=0)

        dialogs = []
        async for dialog in self.iter_allowed_dialogs(limit=None, folder=None):
            if not self._matches_search_scope(
                dialog.entity,
                broadcasts_only=broadcasts_only,
                groups_only=groups_only,
                users_only=users_only,
            ):
                continue
            dialogs.append(dialog)

        merged_messages: list[object] = []
        merged_chats: list[object] = []
        merged_users: list[object] = []
        seen_message_keys: set[tuple[int, int]] = set()

        for dialog in dialogs:
            target = getattr(dialog, "input_entity", None) or await self.client.get_input_entity(dialog.entity)
            result = await self.client(
                functions.messages.SearchRequest(
                    peer=target,
                    q=query,
                    filter=filter,
                    min_date=min_date,
                    max_date=max_date,
                    offset_id=0,
                    add_offset=0,
                    limit=limit,
                    max_id=max_id,
                    min_id=min_id,
                    hash=0,
                    from_id=from_id,
                    saved_peer_id=saved_peer_id,
                    saved_reaction=saved_reaction,
                    top_msg_id=top_msg_id,
                )
            )
            filtered = filter_messages_bundle(
                policy=self.policy,
                messages=result.messages,
                chats=result.chats,
                users=result.users,
                allow_member_listing=self.config.allow_member_listing,
            )
            for message in filtered.messages:
                peer_id = getattr(message, "peer_id", None)
                message_id = getattr(message, "id", None)
                if peer_id is None or message_id is None:
                    continue
                key = (utils.get_peer_id(peer_id), message_id)
                if key in seen_message_keys:
                    continue
                seen_message_keys.add(key)
                merged_messages.append(message)
            merged_chats.extend(filtered.chats)
            merged_users.extend(filtered.users)

        merged_messages.sort(
            key=lambda message: (
                getattr(message, "date", None).timestamp() if getattr(message, "date", None) else 0.0,
                getattr(message, "id", 0),
            ),
            reverse=True,
        )
        merged_messages = self._apply_search_offset(merged_messages, offset_peer=offset_peer, offset_id=offset_id)
        visible_messages = merged_messages[:limit]
        return filter_messages_bundle(
            policy=self.policy,
            messages=visible_messages,
            chats=merged_chats,
            users=merged_users,
            allow_member_listing=self.config.allow_member_listing,
        )

    async def send_message(self, peer: Any, message: str, *, reply_to: int | None = None):
        target = await self.resolve_peer(peer)
        sent = await self.client.send_message(target, message, reply_to=reply_to)
        await self._publish_if_allowed(sent, kind="new_message")
        return sent

    async def send_file(
        self,
        peer: Any,
        *,
        data: bytes,
        file_name: str,
        caption: str = "",
        force_document: bool = False,
        mime_type: str | None = None,
        attributes=None,
        formatting_entities=None,
        thumb: tuple[bytes, str] | None = None,
        reply_to: int | None = None,
    ):
        target = await self.resolve_peer(peer)
        stream = io.BytesIO(data)
        stream.name = file_name
        thumb_stream = None
        if thumb is not None:
            thumb_data, thumb_name = thumb
            thumb_stream = io.BytesIO(thumb_data)
            thumb_stream.name = thumb_name
        sent = await self.client.send_file(
            target,
            stream,
            caption=caption,
            force_document=force_document,
            mime_type=mime_type,
            attributes=attributes,
            formatting_entities=formatting_entities,
            thumb=thumb_stream,
            reply_to=reply_to,
        )
        await self._publish_if_allowed(sent, kind="new_message")
        return sent

    async def mark_read(self, peer: Any):
        target = await self.resolve_peer(peer)
        return await self.client.send_read_acknowledge(target)

    async def read_history(self, peer: Any, max_id: int):
        target = await self.resolve_peer(peer)
        return await self.client(functions.messages.ReadHistoryRequest(peer=target, max_id=max_id))

    async def delete_messages(self, peer: Any, message_ids: list[int], *, revoke: bool = True):
        target = await self.resolve_peer(peer)
        if isinstance(target, types.InputPeerChannel):
            return await self.client(functions.channels.DeleteMessagesRequest(channel=target, id=message_ids))
        return await self.client(functions.messages.DeleteMessagesRequest(id=message_ids, revoke=revoke))

    async def list_participants(self, peer: Any, limit: int = 100):
        target = await self.resolve_peer(peer)
        participants = await self.client.get_participants(target, limit=limit)
        return participants

    async def get_full_chat(self, chat_id: int):
        await self.ensure_connected()
        peer = types.PeerChat(chat_id=chat_id)
        entity = await self.client.get_entity(peer)
        ensure_allowed_peer(self.policy, entity, action="getFullChat")
        return await self.client(functions.messages.GetFullChatRequest(chat_id=chat_id))

    async def get_identity(self) -> dict[str, object]:
        await self.ensure_connected()
        if self.client is None:
            raise RuntimeError("Telegram authentication is required in Telegram -> Settings")
        me = await self.client.get_me()
        full_name = " ".join(part for part in [getattr(me, "first_name", None), getattr(me, "last_name", None)] if part).strip()
        return {
            "id": getattr(me, "id", None),
            "name": full_name or getattr(me, "username", None) or "Unknown",
            "phone": getattr(me, "phone", None),
            "username": getattr(me, "username", None),
        }

    async def apply_authorized_session(
        self,
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        session_string: str,
    ) -> None:
        async with self._connect_lock:
            old_client = self.client
            self.config.upstream_api_id = api_id
            self.config.upstream_api_hash = api_hash
            self.config.upstream_phone = phone
            self.config.upstream_session_string = session_string
            self.client = self._build_client()
            self.policy = CloudPolicySnapshot(folder_name=self.config.cloud_folder_name)
            self._recent_update_keys.clear()
            self._recent_update_index.clear()
            self._handlers_registered = False
            self._register_event_handlers()
            if old_client is not None:
                with contextlib.suppress(Exception):
                    await old_client.disconnect()
        self._ensure_supervisor_task()
        await self.ensure_connected(force_refresh_policy=True)

    async def reset_authorization(self) -> None:
        supervisor = self._supervisor_task
        self._supervisor_task = None
        if supervisor is not None:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        async with self._connect_lock:
            old_client = self.client
            self.config.upstream_api_id = 0
            self.config.upstream_api_hash = ""
            self.config.upstream_phone = ""
            self.config.upstream_session_string = ""
            self.client = None
            self.policy = CloudPolicySnapshot(folder_name=self.config.cloud_folder_name)
            self._recent_update_keys.clear()
            self._recent_update_index.clear()
            self._handlers_registered = False
            if old_client is not None:
                with contextlib.suppress(Exception):
                    await old_client.disconnect()

    async def reset_session(self) -> None:
        supervisor = self._supervisor_task
        self._supervisor_task = None
        if supervisor is not None:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
        async with self._connect_lock:
            old_client = self.client
            self.config.upstream_session_string = ""
            self.client = None
            self.policy = CloudPolicySnapshot(folder_name=self.config.cloud_folder_name)
            self._recent_update_keys.clear()
            self._recent_update_index.clear()
            self._handlers_registered = False
            if old_client is not None:
                with contextlib.suppress(Exception):
                    await old_client.disconnect()

    async def _on_new_message(self, event) -> None:
        await self._publish_if_allowed(event.message, kind="new_message")

    async def _on_message_edited(self, event) -> None:
        await self._publish_if_allowed(event.message, kind="message_edited")

    async def _publish_if_allowed(self, message: types.Message, *, kind: str) -> None:
        peer = getattr(message, "peer_id", None)
        if peer is None or not self.policy.allows_peer(peer):
            return
        fwd = getattr(message, "fwd_from", None)
        if fwd and getattr(fwd, "from_id", None) is not None and not self.policy.allows_peer(fwd.from_id):
            return
        peer_id = self.peer_resolver.peer_id(peer)
        message_id = getattr(message, "id", None)
        update_key = (kind, peer_id, message_id)
        if update_key in self._recent_update_index:
            return
        if len(self._recent_update_keys) == self._recent_update_keys.maxlen:
            old_key = self._recent_update_keys.popleft()
            self._recent_update_index.discard(old_key)
        self._recent_update_keys.append(update_key)
        self._recent_update_index.add(update_key)
        envelope = UpdateEnvelope(
            kind=kind,
            payload=message,
            peer_id=peer_id,
            message_id=message_id,
            mentioned=bool(getattr(message, "mentioned", False)),
            incoming=bool(getattr(message, "out", False) is False),
        )
        await self.update_bus.publish(envelope)
        if envelope.incoming:
            await self.incoming_hook.deliver(
                {
                    "kind": envelope.kind,
                    "peer_id": envelope.peer_id,
                    "message_id": envelope.message_id,
                    "mentioned": envelope.mentioned,
                    "incoming": envelope.incoming,
                    "text": getattr(message, "message", None),
                }
            )

    def _register_event_handlers(self) -> None:
        if self._handlers_registered:
            return
        if self.client is None:
            return
        self.client.add_event_handler(self._on_new_message, events.NewMessage)
        self.client.add_event_handler(self._on_message_edited, events.MessageEdited)
        self._handlers_registered = True

    async def _refresh_policy_locked(self) -> CloudPolicySnapshot:
        self.policy = await build_cloud_policy_snapshot(self.client, self.config.cloud_folder_name)
        logger.info("Loaded Cloud policy with %s peers", len(self.policy.allowed_peers))
        return self.policy

    def _matches_search_scope(
        self,
        entity: object,
        *,
        broadcasts_only: bool | None,
        groups_only: bool | None,
        users_only: bool | None,
    ) -> bool:
        if users_only:
            return isinstance(entity, types.User)
        if groups_only:
            return isinstance(entity, types.Chat) or (
                isinstance(entity, types.Channel) and bool(getattr(entity, "megagroup", False))
            )
        if broadcasts_only:
            return isinstance(entity, types.Channel) and not bool(getattr(entity, "megagroup", False))
        return True

    def _apply_search_offset(
        self,
        messages: list[object],
        *,
        offset_peer,
        offset_id: int,
    ) -> list[object]:
        if offset_id <= 0:
            return messages
        target_peer_id = None
        if offset_peer is not None:
            target_peer_id = utils.get_peer_id(offset_peer)
        for index, message in enumerate(messages):
            message_peer = getattr(message, "peer_id", None)
            message_peer_id = utils.get_peer_id(message_peer) if message_peer is not None else None
            if getattr(message, "id", None) != offset_id:
                continue
            if target_peer_id is not None and message_peer_id != target_peer_id:
                continue
            return messages[index + 1 :]
        return messages

    async def _supervise_connection(self) -> None:
        delay = max(self.config.upstream_reconnect_min_delay, 0.1)
        max_delay = max(delay, self.config.upstream_reconnect_max_delay)
        refresh_on_connect = True
        while not self._stopping:
            try:
                await self.ensure_connected(force_refresh_policy=refresh_on_connect or not self.policy.allowed_peers)
                refresh_on_connect = False
                delay = max(self.config.upstream_reconnect_min_delay, 0.1)
                await self.client.disconnected
                if self._stopping:
                    return
                logger.warning("Upstream Telegram connection dropped; reconnecting")
                refresh_on_connect = True
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                logger.error("Upstream configuration prevents reconnect: %s", exc)
                refresh_on_connect = True
            except UpstreamUnavailableError as exc:
                logger.warning("Upstream Telegram unavailable; retrying in %.1fs: %s", delay, exc)
                refresh_on_connect = True
            except Exception as exc:
                logger.warning("Upstream Telegram reconnect loop failed; retrying in %.1fs: %s", delay, exc)
                refresh_on_connect = True
            if self._stopping:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

    def _ensure_supervisor_task(self) -> None:
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._supervise_connection())
