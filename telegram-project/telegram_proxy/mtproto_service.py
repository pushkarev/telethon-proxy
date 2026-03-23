from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from zlib import crc32

from telethon import functions, types, utils
from telethon.crypto import AES
from telethon.extensions import BinaryReader
from telethon.network.mtprotostate import MTProtoState
from telethon.tl.tlobject import TLObject
from telethon.tl.core import MessageContainer
from telethon.tl.functions import InitConnectionRequest, InvokeWithLayerRequest, InvokeWithoutUpdatesRequest, PingRequest
from telethon.tl.functions.auth import SendCodeRequest, SignInRequest
from telethon.tl.functions.channels import (
    DeleteMessagesRequest as DeleteChannelMessagesRequest,
    GetParticipantsRequest,
    ReadHistoryRequest as ChannelReadHistoryRequest,
)
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.help import GetConfigRequest
from telethon.tl.functions.messages import (
    DeleteMessagesRequest,
    GetDialogsRequest,
    GetFullChatRequest,
    GetHistoryRequest,
    GetPeerDialogsRequest,
    ReadHistoryRequest,
    SearchGlobalRequest,
    SearchRequest,
    SendMediaRequest,
    SendMessageRequest,
)
from telethon.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest
from telethon.tl.functions.updates import GetDifferenceRequest, GetStateRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.tl.types import MsgsAck, Pong, RpcError

from .config import ProxyConfig
from .downstream_auth import DownstreamAuthService
from .downstream_registry import DownstreamRegistry, RegisteredClient
from .session_state import VirtualUpdateState
from .upstream import UpstreamAdapter, UpstreamUnavailableError

logger = logging.getLogger(__name__)


class ProxyRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class MTProtoConnectionState:
    client: RegisteredClient
    session_id: int | None = None
    server_salt: int = 0
    seq_no: int = 0
    packet_seq: int = 0
    last_msg_id: int = 0
    update_state: VirtualUpdateState = field(default_factory=VirtualUpdateState)

    @property
    def principal_phone(self) -> str | None:
        return self.client.phone

    @property
    def is_authorized(self) -> bool:
        return self.client.phone is not None

    def next_msg_id(self) -> int:
        now = time.time()
        nanoseconds = int((now - int(now)) * 1e9)
        msg_id = (int(now) << 32) | ((nanoseconds << 2) | 1)
        if msg_id <= self.last_msg_id:
            msg_id = self.last_msg_id + 4
        self.last_msg_id = msg_id
        return msg_id

    def next_seq_no(self, content_related: bool) -> int:
        if content_related:
            result = self.seq_no * 2 + 1
            self.seq_no += 1
            return result
        return self.seq_no * 2


@dataclass(slots=True)
class ActiveClientConnection:
    connection_id: int
    key_id: int
    label: str
    phone: str | None
    connected_at: datetime
    remote_addr: str
    authorized: bool


class TelethonRequestDispatcher:
    def __init__(
        self,
        upstream: UpstreamAdapter,
        auth: DownstreamAuthService,
        registry: DownstreamRegistry,
        config: ProxyConfig,
    ) -> None:
        self.upstream = upstream
        self.auth = auth
        self.registry = registry
        self.config = config
        self._known_message_peers_by_client: dict[int, dict[int, int]] = {}

    async def dispatch(self, state: MTProtoConnectionState, request: object, *, req_msg_id: int) -> object | None:
        request = self._unwrap_request(request)

        if isinstance(request, PingRequest):
            return Pong(msg_id=req_msg_id, ping_id=request.ping_id)
        if isinstance(request, MsgsAck):
            return None
        if isinstance(request, GetConfigRequest):
            return self._proxy_config()
        if isinstance(request, SendCodeRequest):
            return self._send_code(request)
        if isinstance(request, SignInRequest):
            return self._sign_in(state, request)

        self._require_login(state)

        if isinstance(request, GetUsersRequest):
            return await self._get_users(state, request)
        if isinstance(request, GetStateRequest):
            return state.update_state.snapshot()
        if isinstance(request, GetDifferenceRequest):
            snapshot = state.update_state.snapshot()
            return types.updates.DifferenceEmpty(date=snapshot.date, seq=snapshot.seq)
        if isinstance(request, ResolveUsernameRequest):
            return await self._resolve_username(request)
        if isinstance(request, GetDialogsRequest):
            return await self._get_dialogs(state, request)
        if isinstance(request, GetPeerDialogsRequest):
            return await self._get_peer_dialogs(state, request)
        if isinstance(request, GetFullChatRequest):
            return await self._get_full_chat(request)
        if isinstance(request, GetHistoryRequest):
            return await self._get_history(state, request)
        if isinstance(request, SearchRequest):
            return await self._search_messages(state, request)
        if isinstance(request, SearchGlobalRequest):
            return await self._search_global(state, request)
        if isinstance(request, SendMessageRequest):
            return await self._send_message(state, request)
        if isinstance(request, SendMediaRequest):
            return await self._send_media(state, request)
        if isinstance(request, SaveFilePartRequest):
            return self._save_file_part(state, request)
        if isinstance(request, SaveBigFilePartRequest):
            return self._save_big_file_part(state, request)
        if isinstance(request, ReadHistoryRequest):
            return await self._read_history(state, request)
        if isinstance(request, ChannelReadHistoryRequest):
            return await self._read_channel_history(state, request)
        if isinstance(request, DeleteMessagesRequest):
            return await self._delete_messages(state, request)
        if isinstance(request, DeleteChannelMessagesRequest):
            return await self._delete_channel_messages(state, request)
        if isinstance(request, GetParticipantsRequest):
            return await self._get_participants(request)

        raise ProxyRpcError(400, f"METHOD_NOT_SUPPORTED_{request.__class__.__name__.upper()}")

    def _unwrap_request(self, request: object) -> object:
        while True:
            if isinstance(request, InvokeWithLayerRequest):
                request = request.query
                continue
            if isinstance(request, InitConnectionRequest):
                request = request.query
                continue
            if isinstance(request, InvokeWithoutUpdatesRequest):
                request = request.query
                continue
            return request

    def _require_login(self, state: MTProtoConnectionState) -> None:
        if not state.is_authorized:
            raise ProxyRpcError(401, "AUTH_KEY_UNREGISTERED")

    def _send_code(self, request: SendCodeRequest) -> types.auth.SentCode:
        try:
            result = self.auth.send_code(
                phone=request.phone_number,
                api_id=request.api_id,
                api_hash=request.api_hash,
            )
        except PermissionError as exc:
            raise ProxyRpcError(401, "API_ID_INVALID") from exc
        return types.auth.SentCode(
            type=types.auth.SentCodeTypeApp(length=len(self.config.downstream_login_code)),
            phone_code_hash=result["phone_code_hash"],
            next_type=None,
            timeout=result["timeout"],
        )

    def _sign_in(self, state: MTProtoConnectionState, request: SignInRequest) -> types.auth.Authorization:
        try:
            principal = self.auth.sign_in(
                phone=request.phone_number,
                code=request.phone_code,
                phone_code_hash=request.phone_code_hash,
                password=None,
            )
        except PermissionError as exc:
            message = str(exc)
            if "phone_code_hash" in message:
                raise ProxyRpcError(400, "PHONE_CODE_HASH_INVALID") from exc
            if "expired" in message:
                raise ProxyRpcError(400, "PHONE_CODE_EXPIRED") from exc
            raise ProxyRpcError(400, "PHONE_CODE_INVALID") from exc

        state.client = self.registry.mark_authenticated(state.client.key_id, phone=principal.phone)
        return types.auth.Authorization(user=self._proxy_user(principal.phone))

    async def _get_users(self, state: MTProtoConnectionState, request: GetUsersRequest) -> list[types.User]:
        if len(request.id) == 1 and isinstance(request.id[0], types.InputUserSelf):
            return [self._proxy_user(state.principal_phone or "")]
        raise ProxyRpcError(400, "USER_ID_INVALID")

    async def _resolve_username(self, request: ResolveUsernameRequest) -> types.contacts.ResolvedPeer:
        try:
            return await self.upstream.resolve_username(request.username)
        except PermissionError as exc:
            raise ProxyRpcError(400, "USERNAME_NOT_OCCUPIED") from exc

    async def _get_dialogs(self, state: MTProtoConnectionState, request: GetDialogsRequest) -> types.messages.Dialogs | types.messages.DialogsSlice:
        if request.folder_id not in (None, 0):
            return types.messages.DialogsSlice(count=0, dialogs=[], messages=[], chats=[], users=[])

        users: list[types.User] = []
        chats: list[object] = []
        messages: list[types.Message] = []
        dialogs: list[types.Dialog] = []
        seen_users: set[int] = set()
        seen_chats: set[int] = set()
        seen_messages: set[tuple[int, int]] = set()

        offset_peer = None if isinstance(request.offset_peer, types.InputPeerEmpty) else request.offset_peer
        async for dialog in self.upstream.iter_allowed_dialogs(
            limit=request.limit,
            offset_date=request.offset_date,
            offset_id=request.offset_id,
            offset_peer=offset_peer,
            ignore_pinned=bool(request.exclude_pinned),
            folder=None,
        ):
            dialogs.append(dialog.dialog)
            self._append_entity(dialog.entity, users, chats, seen_users, seen_chats)
            if dialog.message is not None:
                key = (utils.get_peer_id(dialog.message.peer_id), dialog.message.id)
                if key not in seen_messages:
                    messages.append(dialog.message)
                    seen_messages.add(key)
                sender = getattr(dialog.message, "sender", None)
                if sender is not None:
                    self._append_entity(sender, users, chats, seen_users, seen_chats)

        count = len(dialogs)
        self._remember_messages(state, messages)
        return types.messages.DialogsSlice(
            count=count,
            dialogs=dialogs,
            messages=messages,
            chats=chats,
            users=users,
        )

    async def _get_peer_dialogs(self, state: MTProtoConnectionState, request: GetPeerDialogsRequest) -> types.messages.PeerDialogs:
        dialogs_result = await self._get_dialogs(
            state,
            GetDialogsRequest(
                offset_date=None,
                offset_id=0,
                offset_peer=types.InputPeerEmpty(),
                limit=10_000,
                hash=0,
                exclude_pinned=False,
                folder_id=None,
            )
        )
        requested_ids = {
            utils.get_peer_id(dialog_peer.peer)
            for dialog_peer in request.peers
        }
        dialogs = [dialog for dialog in dialogs_result.dialogs if utils.get_peer_id(dialog.peer) in requested_ids]
        top_keys = {(utils.get_peer_id(dialog.peer), dialog.top_message) for dialog in dialogs}
        messages = [
            message
            for message in dialogs_result.messages
            if (utils.get_peer_id(message.peer_id), message.id) in top_keys
        ]
        entity_ids = {utils.get_peer_id(dialog.peer) for dialog in dialogs}
        chats = [chat for chat in dialogs_result.chats if utils.get_peer_id(chat) in entity_ids]
        users = [user for user in dialogs_result.users if utils.get_peer_id(types.PeerUser(user.id)) in entity_ids]
        return types.messages.PeerDialogs(
            dialogs=dialogs,
            messages=messages,
            chats=chats,
            users=users,
            state=VirtualUpdateState().snapshot(),
        )

    async def _get_history(self, state: MTProtoConnectionState, request: GetHistoryRequest) -> types.messages.Messages | types.messages.MessagesSlice:
        try:
            result = await self.upstream.get_history(request.peer, limit=request.limit)
        except PermissionError as exc:
            raise ProxyRpcError(400, "PEER_ID_INVALID") from exc
        self._remember_messages(state, list(result.messages))
        return types.messages.MessagesSlice(
            count=len(result.messages),
            messages=result.messages,
            topics=[],
            chats=result.chats,
            users=result.users,
        )

    async def _get_full_chat(self, request: GetFullChatRequest) -> types.messages.ChatFull:
        try:
            return await self.upstream.get_full_chat(request.chat_id)
        except PermissionError as exc:
            raise ProxyRpcError(400, "CHAT_ID_INVALID") from exc

    async def _search_messages(
        self,
        state: MTProtoConnectionState,
        request: SearchRequest,
    ) -> types.messages.Messages | types.messages.MessagesSlice:
        try:
            result = await self.upstream.search_messages(
                request.peer,
                query=request.q,
                filter=request.filter,
                min_date=request.min_date,
                max_date=request.max_date,
                offset_id=request.offset_id,
                add_offset=request.add_offset,
                limit=request.limit,
                max_id=request.max_id,
                min_id=request.min_id,
                hash_value=request.hash,
                from_id=request.from_id,
                saved_peer_id=request.saved_peer_id,
                saved_reaction=request.saved_reaction,
                top_msg_id=request.top_msg_id,
            )
        except PermissionError as exc:
            raise ProxyRpcError(400, "PEER_ID_INVALID") from exc
        self._remember_messages(state, list(result.messages))
        return types.messages.MessagesSlice(
            count=len(result.messages),
            messages=result.messages,
            topics=[],
            chats=result.chats,
            users=result.users,
        )

    async def _search_global(
        self,
        state: MTProtoConnectionState,
        request: SearchGlobalRequest,
    ) -> types.messages.Messages | types.messages.MessagesSlice:
        result = await self.upstream.search_all_messages(
            query=request.q,
            filter=request.filter,
            min_date=request.min_date,
            max_date=request.max_date,
            offset_peer=None if isinstance(request.offset_peer, types.InputPeerEmpty) else request.offset_peer,
            offset_id=request.offset_id,
            limit=request.limit,
            from_id=None,
            broadcasts_only=request.broadcasts_only,
            groups_only=request.groups_only,
            users_only=request.users_only,
            folder_id=request.folder_id,
        )
        self._remember_messages(state, list(result.messages))
        return types.messages.MessagesSlice(
            count=len(result.messages),
            messages=result.messages,
            topics=[],
            chats=result.chats,
            users=result.users,
        )

    async def _send_message(self, state: MTProtoConnectionState, request: SendMessageRequest) -> types.UpdateShortSentMessage:
        try:
            message = await self.upstream.send_message(request.peer, request.message)
        except PermissionError as exc:
            raise ProxyRpcError(400, "PEER_ID_INVALID") from exc

        self._remember_messages(state, [message])
        new_state = state.update_state.advance_for_message()
        return types.UpdateShortSentMessage(
            id=message.id,
            pts=new_state.pts,
            pts_count=1,
            date=message.date,
            out=True,
            media=getattr(message, "media", None),
            entities=getattr(message, "entities", None),
            ttl_period=getattr(message, "ttl_period", None),
        )

    async def _read_history(self, state: MTProtoConnectionState, request: ReadHistoryRequest) -> types.messages.AffectedMessages:
        try:
            await self.upstream.read_history(request.peer, request.max_id)
        except PermissionError as exc:
            raise ProxyRpcError(400, "PEER_ID_INVALID") from exc

        new_state = state.update_state.advance_for_message()
        return types.messages.AffectedMessages(pts=new_state.pts, pts_count=1)

    async def _read_channel_history(
        self,
        state: MTProtoConnectionState,
        request: ChannelReadHistoryRequest,
    ) -> types.messages.AffectedMessages:
        try:
            await self.upstream.read_history(request.channel, request.max_id)
        except PermissionError as exc:
            raise ProxyRpcError(400, "CHANNEL_PRIVATE") from exc

        new_state = state.update_state.advance_for_message()
        return types.messages.AffectedMessages(pts=new_state.pts, pts_count=1)

    async def _send_media(self, state: MTProtoConnectionState, request: SendMediaRequest) -> types.Updates:
        try:
            payload = self._extract_uploaded_media_payload(state, request.media)
            thumb = self._extract_uploaded_thumb_payload(state, request.media)
            reply_to = getattr(request.reply_to, "reply_to_msg_id", None)
            message = await self.upstream.send_file(
                request.peer,
                data=payload["data"],
                file_name=payload["file_name"],
                caption=request.message or "",
                force_document=payload["force_document"],
                mime_type=payload["mime_type"],
                attributes=payload["attributes"],
                formatting_entities=request.entities,
                thumb=thumb,
                reply_to=reply_to,
            )
        except KeyError as exc:
            raise ProxyRpcError(400, "FILE_PARTS_INVALID") from exc
        except ValueError as exc:
            raise ProxyRpcError(400, "FILE_PARTS_INVALID") from exc
        except PermissionError as exc:
            raise ProxyRpcError(400, "PEER_ID_INVALID") from exc

        self._remember_messages(state, [message])
        new_state = state.update_state.advance_for_message()
        return self._updates_for_sent_message(
            request=request,
            message=message,
            pts=new_state.pts,
            pts_count=1,
        )

    def _save_file_part(self, state: MTProtoConnectionState, request: SaveFilePartRequest) -> bool:
        upload = state.update_state.track_upload(request.file_id, big=False)
        upload.store_part(request.file_part, request.bytes)
        return True

    def _save_big_file_part(self, state: MTProtoConnectionState, request: SaveBigFilePartRequest) -> bool:
        upload = state.update_state.track_upload(request.file_id, big=True)
        upload.store_part(request.file_part, request.bytes, total_parts=request.file_total_parts)
        return True

    async def _delete_messages(self, state: MTProtoConnectionState, request: DeleteMessagesRequest) -> types.messages.AffectedMessages:
        peer = self._resolve_cached_delete_peer(state, request.id)
        try:
            result = await self.upstream.delete_messages(peer, request.id, revoke=request.revoke)
        except PermissionError as exc:
            raise ProxyRpcError(400, "PEER_ID_INVALID") from exc

        new_state = state.update_state.advance_for_message()
        return types.messages.AffectedMessages(pts=new_state.pts, pts_count=result.pts_count or len(request.id))

    async def _delete_channel_messages(
        self,
        state: MTProtoConnectionState,
        request: DeleteChannelMessagesRequest,
    ) -> types.messages.AffectedMessages:
        try:
            result = await self.upstream.delete_messages(request.channel, request.id, revoke=True)
        except PermissionError as exc:
            raise ProxyRpcError(400, "CHANNEL_PRIVATE") from exc

        new_state = state.update_state.advance_for_message()
        return types.messages.AffectedMessages(pts=new_state.pts, pts_count=result.pts_count or len(request.id))

    async def _get_participants(self, request: GetParticipantsRequest) -> types.channels.ChannelParticipants:
        try:
            users = await self.upstream.list_participants(request.channel, limit=request.limit)
            chat = await self.upstream.client.get_entity(request.channel)
        except PermissionError as exc:
            raise ProxyRpcError(400, "CHANNEL_PRIVATE") from exc

        chats = []
        if isinstance(chat, (types.Chat, types.Channel)):
            chats.append(chat)
        return types.channels.ChannelParticipants(
            count=len(users),
            participants=[],
            chats=chats,
            users=users,
        )

    def _extract_uploaded_media_payload(self, state: MTProtoConnectionState, media: object) -> dict[str, object]:
        if isinstance(media, types.InputMediaUploadedPhoto):
            upload = state.update_state.take_upload(media.file.id)
            if upload.total_parts is None:
                upload.total_parts = media.file.parts
            return {
                "data": upload.assemble(),
                "file_name": media.file.name,
                "force_document": False,
                "mime_type": None,
                "attributes": None,
            }
        if isinstance(media, types.InputMediaUploadedDocument):
            upload = state.update_state.take_upload(media.file.id)
            if upload.total_parts is None:
                upload.total_parts = media.file.parts
            return {
                "data": upload.assemble(),
                "file_name": media.file.name,
                "force_document": True,
                "mime_type": media.mime_type,
                "attributes": media.attributes,
            }
        raise ProxyRpcError(400, "MEDIA_INVALID")

    def _extract_uploaded_thumb_payload(
        self,
        state: MTProtoConnectionState,
        media: object,
    ) -> tuple[bytes, str] | None:
        thumb = getattr(media, "thumb", None)
        if thumb is None:
            return None
        upload = state.update_state.take_upload(thumb.id)
        if upload.total_parts is None:
            upload.total_parts = thumb.parts
        return upload.assemble(), thumb.name

    def _resolve_cached_delete_peer(self, state: MTProtoConnectionState, message_ids: list[int]) -> object:
        client_cache = self._known_message_peers_by_client.get(state.client.key_id, {})
        resolved_peers = [
            state.update_state.known_message_peers.get(message_id) or client_cache.get(message_id)
            for message_id in message_ids
        ]
        if any(peer_id is None for peer_id in resolved_peers):
            raise ProxyRpcError(400, "MESSAGE_ID_INVALID")
        peer_ids = set(resolved_peers)
        if len(peer_ids) != 1:
            raise ProxyRpcError(400, "MESSAGE_ID_INVALID")
        peer_id = peer_ids.pop()
        real_id, peer_type = utils.resolve_id(peer_id)
        return peer_type(real_id)

    def _remember_messages(self, state: MTProtoConnectionState, messages: list[object]) -> None:
        state.update_state.remember_messages(messages)
        cache = self._known_message_peers_by_client.setdefault(state.client.key_id, {})
        for message in messages:
            message_id = getattr(message, "id", None)
            peer = getattr(message, "peer_id", None)
            if message_id is None or peer is None:
                continue
            cache[message_id] = utils.get_peer_id(peer)

    def _updates_for_sent_message(
        self,
        *,
        request: SendMediaRequest,
        message: types.Message,
        pts: int,
        pts_count: int,
    ) -> types.Updates:
        users: list[types.User] = []
        chats: list[object] = []
        seen_users: set[int] = set()
        seen_chats: set[int] = set()

        for entity in (getattr(message, "sender", None), getattr(message, "chat", None)):
            if entity is not None:
                self._append_entity(entity, users, chats, seen_users, seen_chats)

        update_cls = types.UpdateNewChannelMessage if isinstance(message.peer_id, types.PeerChannel) else types.UpdateNewMessage
        return types.Updates(
            updates=[
                types.UpdateMessageID(random_id=request.random_id, id=message.id),
                update_cls(message=message, pts=pts, pts_count=pts_count),
            ],
            users=users,
            chats=chats,
            date=message.date,
            seq=0,
        )

    def _append_entity(
        self,
        entity: object,
        users: list[types.User],
        chats: list[object],
        seen_users: set[int],
        seen_chats: set[int],
    ) -> None:
        if isinstance(entity, types.User):
            if entity.id not in seen_users:
                users.append(entity)
                seen_users.add(entity.id)
            return
        if isinstance(entity, (types.Chat, types.Channel)):
            key = utils.get_peer_id(entity)
            if key not in seen_chats:
                chats.append(entity)
                seen_chats.add(key)

    def _proxy_user(self, phone: str) -> types.User:
        return types.User(
            id=1,
            is_self=True,
            access_hash=1,
            first_name="Proxy",
            last_name="User",
            username=None,
            phone=phone,
        )

    def _proxy_config(self) -> types.Config:
        now = datetime.now(timezone.utc)
        return types.Config(
            date=now,
            expires=now,
            test_mode=False,
            this_dc=2,
            dc_options=[
                types.DcOption(
                    id=2,
                    ip_address=self.config.mtproto_host,
                    port=self.config.mtproto_port,
                    static=True,
                    this_port_only=True,
                    tcpo_only=False,
                )
            ],
            dc_txt_domain_name="localhost",
            chat_size_max=200,
            megagroup_size_max=200_000,
            forwarded_count_max=100,
            online_update_period_ms=30_000,
            offline_blur_timeout_ms=5_000,
            offline_idle_timeout_ms=30_000,
            online_cloud_timeout_ms=30_000,
            notify_cloud_delay_ms=1_500,
            notify_default_delay_ms=1_500,
            push_chat_period_ms=60_000,
            push_chat_limit=2,
            edit_time_limit=172800,
            revoke_time_limit=172800,
            revoke_pm_time_limit=172800,
            rating_e_decay=2419200,
            stickers_recent_limit=30,
            channels_read_media_period=604800,
            call_receive_timeout_ms=20_000,
            call_ring_timeout_ms=90_000,
            call_connect_timeout_ms=30_000,
            call_packet_timeout_ms=10_000,
            me_url_prefix="https://t.me/",
            caption_length_max=1024,
            message_length_max=4096,
            webfile_dc_id=2,
            default_p2p_contacts=False,
            preload_featured_stickers=False,
            revoke_pm_inbox=False,
            blocked_mode=False,
            force_try_ipv6=False,
            tmp_sessions=0,
            autoupdate_url_prefix="",
            gif_search_username="",
            venue_search_username="",
            img_search_username="",
            static_maps_provider="",
            suggested_lang_code="en",
            lang_pack_version=0,
            base_lang_pack_version=0,
            reactions_default=None,
            autologin_token=None,
        )


class MTProtoProxyServer:
    def __init__(
        self,
        config: ProxyConfig,
        upstream: UpstreamAdapter,
        auth: DownstreamAuthService,
        registry: DownstreamRegistry,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.auth = auth
        self.registry = registry
        self.dispatcher = TelethonRequestDispatcher(upstream, auth, registry, config)
        self._server: asyncio.AbstractServer | None = None
        self._active_connections: dict[int, ActiveClientConnection] = {}
        self._next_connection_id = 1

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.mtproto_host,
            port=self.config.mtproto_port,
        )
        if self._server.sockets:
            self.config.mtproto_port = self._server.sockets[0].getsockname()[1]
        sockets = ", ".join(str(sock.getsockname()) for sock in (self._server.sockets or []))
        logger.info("MTProto proxy listening on %s", sockets)

    async def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        server.close()
        await server.wait_closed()

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("MTProto server not started")
        async with self._server:
            await self._server.serve_forever()

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        state: MTProtoConnectionState | None = None
        update_queue: asyncio.Queue | None = None
        push_task: asyncio.Task[None] | None = None
        writer_lock = asyncio.Lock()
        connection_id: int | None = None
        try:
            while not reader.at_eof():
                packet = await self._read_packet(reader)
                auth_key_id = struct.unpack("<Q", packet[:8])[0]
                if auth_key_id == 0:
                    logger.warning("Client attempted plaintext MTProto auth; issue a proxy session first")
                    break

                client = self.registry.get_client(auth_key_id)
                if client is None:
                    logger.warning("Rejected unknown downstream auth key id=%s", auth_key_id)
                    break

                if state is None:
                    state = MTProtoConnectionState(client=client)
                    connection_id = self._register_connection(client, writer)
                    update_bus = getattr(self.upstream, "update_bus", None)
                    if update_bus is not None:
                        update_queue = update_bus.subscribe()
                        push_task = asyncio.create_task(self._push_updates(state, writer, writer_lock, update_queue))
                elif state.client.key_id != client.key_id:
                    logger.warning("Client changed auth key mid-connection")
                    break
                plain = self._decrypt_client_packet(client.auth_key.key, packet)
                responses = await self._dispatch_packet(state, plain)
                if connection_id is not None:
                    self._update_connection_state(connection_id, state)
                for body, content_related in responses:
                    await self._write_payload(state, writer, writer_lock, body=body, content_related=content_related)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if connection_id is not None:
                self._active_connections.pop(connection_id, None)
            if update_queue is not None:
                update_bus = getattr(self.upstream, "update_bus", None)
                if update_bus is not None:
                    update_bus.unsubscribe(update_queue)
            if push_task is not None:
                push_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await push_task
            writer.close()
            await writer.wait_closed()

    def active_connections_snapshot(self) -> list[dict[str, object]]:
        connections = sorted(self._active_connections.values(), key=lambda item: item.connected_at, reverse=True)
        return [
            {
                "connection_id": item.connection_id,
                "key_id": item.key_id,
                "label": item.label,
                "phone": item.phone,
                "connected_at": item.connected_at.isoformat(),
                "remote_addr": item.remote_addr,
                "authorized": item.authorized,
            }
            for item in connections
        ]

    async def _dispatch_packet(self, state: MTProtoConnectionState, plain: bytes) -> list[tuple[bytes, bool]]:
        reader = BinaryReader(plain)
        reader.read_long()  # salt
        session_id = reader.read_long()
        if state.session_id is None:
            state.session_id = session_id
        req_msg_id = reader.read_long()
        reader.read_int()  # seqno
        msg_len = reader.read_int()
        body = reader.read(msg_len)

        with BinaryReader(body) as body_reader:
            obj = body_reader.tgread_object()

        return await self._dispatch_object(state, obj, req_msg_id=req_msg_id)

    async def _dispatch_object(
        self,
        state: MTProtoConnectionState,
        obj: object,
        *,
        req_msg_id: int,
    ) -> list[tuple[bytes, bool]]:
        if isinstance(obj, MessageContainer):
            responses: list[tuple[bytes, bool]] = []
            ack_ids: list[int] = []
            for message in obj.messages:
                if message.seq_no % 2 == 1:
                    ack_ids.append(message.msg_id)
                responses.extend(await self._dispatch_object(state, message.obj, req_msg_id=message.msg_id))
            if ack_ids:
                responses.append((bytes(MsgsAck(ack_ids)), False))
            return responses

        try:
            result = await self.dispatcher.dispatch(state, obj, req_msg_id=req_msg_id)
        except UpstreamUnavailableError:
            return [(self._serialize_rpc_result(req_msg_id, error=RpcError(500, "UPSTREAM_UNAVAILABLE")), True)]
        except ProxyRpcError as exc:
            return [(self._serialize_rpc_result(req_msg_id, error=RpcError(exc.code, exc.message)), True)]

        if result is None:
            return []

        return [(self._serialize_rpc_result(req_msg_id, body=self._serialize_result(result)), True)]

    async def _read_packet(self, reader: asyncio.StreamReader) -> bytes:
        header = await reader.readexactly(8)
        packet_len, _seq = struct.unpack("<ii", header)
        if packet_len < 12:
            raise ConnectionError(f"invalid packet length: {packet_len}")
        body = await reader.readexactly(packet_len - 8)
        checksum = struct.unpack("<I", body[-4:])[0]
        payload = body[:-4]
        valid = crc32(header + payload)
        if checksum != valid:
            raise ConnectionError("invalid TCP full checksum")
        return payload

    def _encode_packet(self, state: MTProtoConnectionState, payload: bytes) -> bytes:
        packet_len = len(payload) + 12
        frame = struct.pack("<ii", packet_len, state.packet_seq) + payload
        checksum = struct.pack("<I", crc32(frame))
        state.packet_seq += 1
        return frame + checksum

    async def _write_payload(
        self,
        state: MTProtoConnectionState,
        writer: asyncio.StreamWriter,
        writer_lock: asyncio.Lock,
        *,
        body: bytes,
        content_related: bool,
    ) -> None:
        async with writer_lock:
            payload = self._encode_packet(state, self._encrypt_server_payload(state, body, content_related))
            writer.write(payload)
            await writer.drain()

    async def _push_updates(
        self,
        state: MTProtoConnectionState,
        writer: asyncio.StreamWriter,
        writer_lock: asyncio.Lock,
        queue: asyncio.Queue,
    ) -> None:
        while True:
            envelope = await queue.get()
            if not state.is_authorized:
                continue
            body = await self._serialize_update_envelope(state, envelope)
            if body is None:
                continue
            await self._write_payload(state, writer, writer_lock, body=body, content_related=True)

    async def _serialize_update_envelope(self, state: MTProtoConnectionState, envelope) -> bytes | None:
        message = envelope.payload
        if not isinstance(message, types.Message):
            return None

        users, chats = await self._collect_update_entities(message)
        new_state = state.update_state.advance_for_message()
        update = self._build_message_update(
            kind=envelope.kind,
            message=message,
            pts=new_state.pts,
            pts_count=1,
        )
        if update is None:
            return None

        return bytes(
            types.Updates(
                updates=[update],
                users=users,
                chats=chats,
                date=message.date,
                seq=0,
            )
        )

    async def _collect_update_entities(self, message: types.Message) -> tuple[list[types.User], list[object]]:
        users: list[types.User] = []
        chats: list[object] = []
        seen_users: set[int] = set()
        seen_chats: set[int] = set()

        for entity in (getattr(message, "sender", None), getattr(message, "chat", None)):
            if entity is not None:
                self.dispatcher._append_entity(entity, users, chats, seen_users, seen_chats)

        for ref in (getattr(message, "peer_id", None), getattr(message, "from_id", None)):
            if ref is None:
                continue
            try:
                entity = await self.upstream.client.get_entity(ref)
            except Exception:
                continue
            self.dispatcher._append_entity(entity, users, chats, seen_users, seen_chats)

        return users, chats

    def _build_message_update(
        self,
        *,
        kind: str,
        message: types.Message,
        pts: int,
        pts_count: int,
    ) -> object | None:
        if kind == "new_message":
            if isinstance(message.peer_id, types.PeerChannel):
                return types.UpdateNewChannelMessage(message=message, pts=pts, pts_count=pts_count)
            return types.UpdateNewMessage(message=message, pts=pts, pts_count=pts_count)
        if kind == "message_edited":
            if isinstance(message.peer_id, types.PeerChannel):
                return types.UpdateEditChannelMessage(message=message, pts=pts, pts_count=pts_count)
            return types.UpdateEditMessage(message=message, pts=pts, pts_count=pts_count)
        return None

    def _decrypt_client_packet(self, auth_key: bytes, packet: bytes) -> bytes:
        if len(packet) < 24:
            raise ConnectionError("encrypted packet too small")
        msg_key = packet[8:24]
        aes_key, aes_iv = MTProtoState._calc_key(auth_key, msg_key, True)
        plain = AES.decrypt_ige(packet[24:], aes_key, aes_iv)
        expected = sha256(auth_key[88:120] + plain).digest()[8:24]
        if expected != msg_key:
            raise ConnectionError("invalid MTProto message key")
        return plain

    def _encrypt_server_payload(
        self,
        state: MTProtoConnectionState,
        body: bytes,
        content_related: bool,
    ) -> bytes:
        auth_key = state.client.auth_key.key
        session_id = state.session_id or 0
        msg_id = state.next_msg_id()
        seq_no = state.next_seq_no(content_related)
        plain = (
            struct.pack("<q", state.server_salt)
            + struct.pack("<q", session_id)
            + struct.pack("<q", msg_id)
            + struct.pack("<i", seq_no)
            + struct.pack("<i", len(body))
            + body
        )
        padding = os.urandom((-(len(plain) + 12) % 16) + 12)
        payload = plain + padding
        msg_key = sha256(auth_key[96:128] + payload).digest()[8:24]
        aes_key, aes_iv = MTProtoState._calc_key(auth_key, msg_key, False)
        encrypted = AES.encrypt_ige(payload, aes_key, aes_iv)
        key_id = struct.pack("<Q", state.client.auth_key.key_id)
        return key_id + msg_key + encrypted

    def _serialize_rpc_result(
        self,
        req_msg_id: int,
        *,
        body: bytes | None = None,
        error: RpcError | None = None,
    ) -> bytes:
        if (body is None) == (error is None):
            raise ValueError("exactly one of body or error must be set")
        payload = body if body is not None else bytes(error)
        return b"\x01m\\\xf3" + struct.pack("<q", req_msg_id) + payload

    def _serialize_result(self, result: object) -> bytes:
        if isinstance(result, bool):
            return bytes.fromhex("b5757299" if result else "379779bc")
        if isinstance(result, list):
            return b"\x15\xc4\xb5\x1c" + struct.pack("<i", len(result)) + b"".join(bytes(item) for item in result)
        if isinstance(result, TLObject):
            return bytes(result)
        raise TypeError(f"Cannot serialize result type: {type(result)!r}")

    def _register_connection(self, client: RegisteredClient, writer: asyncio.StreamWriter) -> int:
        connection_id = self._next_connection_id
        self._next_connection_id += 1
        peer = writer.get_extra_info("peername")
        remote_addr = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) and len(peer) >= 2 else "unknown"
        self._active_connections[connection_id] = ActiveClientConnection(
            connection_id=connection_id,
            key_id=client.key_id,
            label=client.label,
            phone=client.phone,
            connected_at=datetime.now(timezone.utc),
            remote_addr=remote_addr,
            authorized=client.phone is not None,
        )
        return connection_id

    def _update_connection_state(self, connection_id: int, state: MTProtoConnectionState) -> None:
        connection = self._active_connections.get(connection_id)
        if connection is None:
            return
        connection.label = state.client.label
        connection.phone = state.client.phone
        connection.authorized = state.client.phone is not None
