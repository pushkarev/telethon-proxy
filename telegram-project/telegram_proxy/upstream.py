from __future__ import annotations

import asyncio
import logging
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

    async def start(self) -> None:
        if not self.config.upstream_api_id or not self.config.upstream_api_hash:
            raise RuntimeError("Missing upstream Telegram credentials")
        await self.client.start(phone=self.config.upstream_phone or None)
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

    async def resolve_peer(self, peer: Any) -> Any:
        normalized = self.peer_resolver.normalize_peer_ref(peer)
        if normalized == "me":
            return "me"
        entity = await self.client.get_input_entity(normalized)
        ensure_allowed_peer(self.policy, entity, action="resolvePeer")
        return entity

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
        return await self.client.send_message(target, message)

    async def mark_read(self, peer: Any):
        target = await self.resolve_peer(peer)
        return await self.client.send_read_acknowledge(target)

    async def list_participants(self, peer: Any, limit: int = 100):
        target = await self.resolve_peer(peer)
        participants = await self.client.get_participants(target, limit=limit)
        return participants

    async def _on_new_message(self, event) -> None:
        await self._publish_if_allowed(event.message, kind="new_message")

    async def _on_message_edited(self, event) -> None:
        await self._publish_if_allowed(event.message, kind="message_edited")

    async def _publish_if_allowed(self, message: types.Message, *, kind: str) -> None:
        peer = getattr(message, "peer_id", None)
        if peer is None or not self.policy.allows_peer(peer):
            return
        peer_id = self.peer_resolver.peer_id(peer)
        envelope = UpdateEnvelope(
            kind=kind,
            payload=message,
            peer_id=peer_id,
            message_id=getattr(message, "id", None),
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
