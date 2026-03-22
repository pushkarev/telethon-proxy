from __future__ import annotations

import asyncio
import io
import logging
from collections import deque
from typing import Any

from telethon import TelegramClient, events, functions, types

from .config import ProxyConfig
from .filtering import ensure_allowed_peer, filter_messages_bundle
from .folders import build_cloud_policy_snapshot
from .hooks import IncomingHook
from .peer_refs import PeerResolver
from .policy import CloudPolicySnapshot
from .update_bus import UpdateBus, UpdateEnvelope

logger = logging.getLogger(__name__)


class UpstreamAdapter:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self.client = TelegramClient(
            str(config.upstream_session_path),
            config.upstream_api_id,
            config.upstream_api_hash,
        )
        self.policy = CloudPolicySnapshot(folder_name=config.cloud_folder_name)
        self.update_bus = UpdateBus(buffer_size=config.update_buffer_size)
        self.peer_resolver = PeerResolver()
        self.incoming_hook = IncomingHook(config.incoming_hook_command)
        self._policy_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._recent_update_keys: deque[tuple[str, int | None, int | None]] = deque(maxlen=max(config.update_buffer_size, 100))
        self._recent_update_index: set[tuple[str, int | None, int | None]] = set()

    async def start(self) -> None:
        if not self.config.upstream_api_id or not self.config.upstream_api_hash:
            raise RuntimeError("Missing upstream Telegram credentials")
        await self.client.connect()
        if not await self.client.is_user_authorized():
            if not self.config.upstream_phone:
                raise RuntimeError("Missing upstream phone number for unauthorized Telegram session")
            await self.client.start(phone=self.config.upstream_phone)
        await self.refresh_policy()
        self.client.add_event_handler(self._on_new_message, events.NewMessage)
        self.client.add_event_handler(self._on_message_edited, events.MessageEdited)
        logger.info("Upstream adapter started")

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
        await self.client.disconnect()

    async def refresh_policy(self) -> CloudPolicySnapshot:
        async with self._policy_lock:
            self.policy = await build_cloud_policy_snapshot(self.client, self.config.cloud_folder_name)
            logger.info("Loaded Cloud policy with %s peers", len(self.policy.allowed_peers))
            return self.policy

    async def get_dialogs(self, limit: int = 100):
        dialogs = await self.client.get_dialogs(limit=limit)
        return [dialog for dialog in dialogs if self.policy.allows_peer(dialog.entity)]

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
        # The upstream Telethon iterator path is brittle around offset_peer handling
        # in our proxy use case, so we currently fetch a stable slice and filter it.
        dialogs = await self.client.get_dialogs(
            limit=limit or 1_000,
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
        normalized = self.peer_resolver.normalize_peer_ref(peer)
        entity = await self.client.get_input_entity(normalized)
        ensure_allowed_peer(self.policy, entity, action="resolvePeer")
        return entity

    async def resolve_username(self, username: str):
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

    async def send_message(self, peer: Any, message: str):
        target = await self.resolve_peer(peer)
        sent = await self.client.send_message(target, message)
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
        peer = types.PeerChat(chat_id=chat_id)
        entity = await self.client.get_entity(peer)
        ensure_allowed_peer(self.policy, entity, action="getFullChat")
        return await self.client(functions.messages.GetFullChatRequest(chat_id=chat_id))

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
