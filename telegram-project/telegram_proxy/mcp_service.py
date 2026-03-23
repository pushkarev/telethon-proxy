from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import ssl
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from telethon import types, utils

from .config import ProxyConfig
from .imessage_bridge import IMessageBridge, IMessageBridgeError
from .update_bus import UpdateEnvelope
from .upstream import UpstreamAdapter, UpstreamUnavailableError
from .whatsapp_bridge import WhatsAppBridge, WhatsAppBridgeError

SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
}
SERVER_PROTOCOL_VERSION = "2025-06-18"
SESSION_HEADER = "mcp-session-id"


class McpHttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(slots=True)
class McpSession:
    session_id: str
    created_at: datetime
    protocol_version: str
    client_name: str | None = None
    subscriptions: set[str] = field(default_factory=set)
    writer: asyncio.StreamWriter | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class McpServer:
    def __init__(
        self,
        config: ProxyConfig,
        upstream: UpstreamAdapter,
        *,
        whatsapp: WhatsAppBridge | None = None,
        imessage: IMessageBridge | None = None,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.whatsapp = whatsapp
        self.imessage = imessage
        self._server: asyncio.AbstractServer | None = None
        self._sessions: dict[str, McpSession] = {}
        self._recent_updates: deque[dict[str, object]] = deque(maxlen=max(config.update_buffer_size, 200))
        self._update_queue: asyncio.Queue[UpdateEnvelope] | None = None
        self._update_task: asyncio.Task[None] | None = None

    def _imessage_enabled(self) -> bool:
        return self.imessage is not None and self.config.imessage_enabled

    async def start(self) -> None:
        if self._server is not None:
            return
        ssl_context = self._ssl_context()
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.mcp_host,
            port=self.config.mcp_port,
            ssl=ssl_context,
        )
        if self._server.sockets:
            self.config.mcp_port = self._server.sockets[0].getsockname()[1]
        self._update_queue = self.upstream.update_bus.subscribe()
        self._update_task = asyncio.create_task(self._fan_out_updates())

    async def stop(self) -> None:
        if self._update_task is not None:
            self._update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._update_task
            self._update_task = None
        if self._update_queue is not None:
            self.upstream.update_bus.unsubscribe(self._update_queue)
            self._update_queue = None
        for session in self._sessions.values():
            if session.writer is not None:
                session.writer.close()
                with contextlib.suppress(Exception):
                    await session.writer.wait_closed()
        self._sessions.clear()
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    def _ssl_context(self) -> ssl.SSLContext | None:
        self.config.validate_mcp_tls_config()
        if self.config.mcp_scheme != "https":
            return None
        cert_path = self.config.mcp_tls_cert_path
        key_path = self.config.mcp_tls_key_path
        assert cert_path is not None
        assert key_path is not None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        return context

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        keep_open = False
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, target, _version = request_line.decode("ascii", errors="replace").strip().split(" ", 2)
            headers = await self._read_headers(reader)
            body = b""
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length > 0:
                body = await reader.readexactly(content_length)

            try:
                self._validate_origin(headers)
                self._validate_auth(headers)
                keep_open = await self._dispatch(method, target, headers, body, writer)
            except McpHttpError as exc:
                await self._write_json_response(writer, exc.status, {"error": exc.message})
        finally:
            if not keep_open:
                writer.close()
                await writer.wait_closed()

    async def _dispatch(
        self,
        method: str,
        target: str,
        headers: dict[str, str],
        body: bytes,
        writer: asyncio.StreamWriter,
    ) -> bool:
        url = urlsplit(target)
        if url.path != self.config.mcp_path:
            raise McpHttpError(404, "Not Found")

        if method == "GET":
            accept = headers.get("accept", "")
            if "text/event-stream" in accept:
                session = self._require_session(headers)
                await self._open_event_stream(session, writer)
                return True
            await self._write_json_response(
                writer,
                200,
                {
                    "transport": "http+sse",
                    "name": "telethon-proxy-mcp",
                    "mcp_path": self.config.mcp_path,
                },
            )
            return False

        if method == "DELETE":
            session = self._require_session(headers)
            self._sessions.pop(session.session_id, None)
            await self._write_json_response(writer, 200, {"ok": True})
            return False

        if method != "POST":
            raise McpHttpError(405, "Method Not Allowed")
        if not body:
            raise McpHttpError(400, "Missing JSON-RPC body")

        try:
            request = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise McpHttpError(400, f"Invalid JSON: {exc}") from exc

        if request.get("method") == "initialize":
            session = self._create_session()
        else:
            session = self._require_session(headers)

        response = await self._handle_rpc(request, session)
        headers_out = {}
        if request.get("method") == "initialize" and response is not None and "result" in response:
            headers_out["Mcp-Session-Id"] = session.session_id
        if response is None:
            await self._write_json_response(writer, 202, {"ok": True}, extra_headers=headers_out)
            return False
        await self._write_json_response(writer, 200, response, extra_headers=headers_out)
        return False

    async def _handle_rpc(self, request: dict[str, object], session: McpSession | None) -> dict[str, object] | None:
        rpc_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        try:
            if method == "notifications/initialized":
                return None
            if method == "initialize":
                assert session is not None
                client_protocol = str(params.get("protocolVersion") or SERVER_PROTOCOL_VERSION)
                session.protocol_version = (
                    client_protocol if client_protocol in SUPPORTED_PROTOCOL_VERSIONS else SERVER_PROTOCOL_VERSION
                )
                client_info = params.get("clientInfo") or {}
                if isinstance(client_info, dict):
                    session.client_name = str(client_info.get("name") or "") or None
                return self._result(
                    rpc_id,
                    {
                        "protocolVersion": session.protocol_version,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"listChanged": False, "subscribe": True},
                        },
                        "serverInfo": {
                            "name": "telethon-proxy-mcp",
                            "version": "0.2.0",
                        },
                        "instructions": (
                            "Cloud-scoped Telegram access, WhatsApp chats carrying the Cloud label, "
                            "and local iMessage chats from the Messages app. "
                            "Use resources/subscribe with an SSE GET stream to receive update notifications."
                        ),
                    },
                )
            if method == "ping":
                return self._result(rpc_id, {})
            if method == "tools/list":
                return self._result(rpc_id, {"tools": self._tools()})
            if method == "tools/call":
                return self._result(rpc_id, await self._call_tool(params))
            if method == "resources/list":
                return self._result(rpc_id, {"resources": await self._resources()})
            if method == "resources/read":
                return self._result(rpc_id, await self._read_resource(params))
            if method == "resources/subscribe":
                assert session is not None
                uri = str(params.get("uri") or "")
                self._validate_resource_uri(uri)
                session.subscriptions.add(uri)
                return self._result(rpc_id, {})
            if method == "resources/unsubscribe":
                assert session is not None
                uri = str(params.get("uri") or "")
                session.subscriptions.discard(uri)
                return self._result(rpc_id, {})
            return self._error(rpc_id, -32601, f"Method not found: {method}")
        except McpHttpError as exc:
            return self._error(rpc_id, -32000, exc.message)
        except WhatsAppBridgeError as exc:
            return self._error(rpc_id, -32000, f"WhatsApp unavailable: {exc}")
        except IMessageBridgeError as exc:
            return self._error(rpc_id, -32000, f"iMessage unavailable: {exc}")

    async def _call_tool(self, params: dict[str, object]) -> dict[str, object]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        try:
            if name == "telegram.list_chats":
                payload = await self._tool_list_chats(limit=int(arguments.get("limit", 100)))
            elif name == "telegram.get_messages":
                payload = await self._tool_get_messages(peer=self._require_peer(arguments), limit=int(arguments.get("limit", 50)))
            elif name == "telegram.search_messages":
                payload = await self._tool_search_messages(
                    query=str(arguments.get("query") or ""),
                    peer=arguments.get("peer"),
                    limit=int(arguments.get("limit", 20)),
                )
            elif name == "telegram.send_message":
                payload = await self._tool_send_message(
                    peer=self._require_peer(arguments),
                    text=str(arguments.get("text") or ""),
                    reply_to_message_id=self._optional_int(arguments.get("reply_to_message_id")),
                )
            elif name == "telegram.delete_messages":
                payload = await self._tool_delete_messages(
                    peer=self._require_peer(arguments),
                    message_ids=self._require_message_ids(arguments),
                )
            elif name == "telegram.mark_read":
                payload = await self._tool_mark_read(
                    peer=self._require_peer(arguments),
                    max_id=self._optional_int(arguments.get("max_id")) or 0,
                )
            elif name == "telegram.list_members":
                payload = await self._tool_list_members(peer=self._require_peer(arguments), limit=int(arguments.get("limit", 100)))
            elif name == "telegram.get_updates":
                payload = await self._tool_get_updates(limit=int(arguments.get("limit", 50)))
            elif name == "whatsapp.list_chats":
                payload = await self._tool_whatsapp_list_chats(limit=int(arguments.get("limit", 100)))
            elif name == "whatsapp.get_auth_status":
                payload = await self._tool_whatsapp_get_auth_status()
            elif name == "whatsapp.get_messages":
                payload = await self._tool_whatsapp_get_messages(
                    jid=self._require_whatsapp_jid(arguments),
                    limit=int(arguments.get("limit", 50)),
                )
            elif name == "whatsapp.send_message":
                payload = await self._tool_whatsapp_send_message(
                    jid=self._require_whatsapp_jid(arguments),
                    text=str(arguments.get("text") or ""),
                )
            elif name == "whatsapp.mark_read":
                payload = await self._tool_whatsapp_mark_read(
                    jid=self._require_whatsapp_jid(arguments),
                    message_id=self._optional_str(arguments.get("message_id")),
                )
            elif name == "whatsapp.get_updates":
                payload = await self._tool_whatsapp_get_updates(limit=int(arguments.get("limit", 50)))
            elif name == "imessage.list_chats":
                payload = await self._tool_imessage_list_chats(limit=int(arguments.get("limit", 100)))
            elif name == "imessage.get_auth_status":
                payload = await self._tool_imessage_get_auth_status()
            elif name == "imessage.get_messages":
                payload = await self._tool_imessage_get_messages(
                    chat_id=self._require_imessage_chat_id(arguments),
                    limit=int(arguments.get("limit", 50)),
                )
            elif name == "imessage.send_message":
                payload = await self._tool_imessage_send_message(
                    chat_id=self._require_imessage_chat_id(arguments),
                    text=str(arguments.get("text") or ""),
                )
            elif name == "imessage.get_updates":
                payload = await self._tool_imessage_get_updates(limit=int(arguments.get("limit", 50)))
            else:
                raise McpHttpError(404, f"Unknown tool: {name}")
        except PermissionError as exc:
            payload = {"ok": False, "error": str(exc)}
        except UpstreamUnavailableError as exc:
            payload = {"ok": False, "error": f"Upstream unavailable: {exc}"}
        except WhatsAppBridgeError as exc:
            payload = {"ok": False, "error": f"WhatsApp unavailable: {exc}"}
        except IMessageBridgeError as exc:
            payload = {"ok": False, "error": f"iMessage unavailable: {exc}"}

        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "structuredContent": payload,
            "isError": not bool(payload.get("ok", True)),
        }

    async def _resources(self) -> list[dict[str, object]]:
        dialogs = await self.upstream.get_dialogs(limit=500)
        resources = [
            {
                "uri": "telegram://config",
                "name": "Proxy configuration",
                "mimeType": "application/json",
                "description": "Configuration and identity summary for the Telegram proxy.",
            },
            {
                "uri": "telegram://chats",
                "name": "Accessible chats",
                "mimeType": "application/json",
                "description": "Chats visible through the Cloud folder policy.",
            },
            {
                "uri": "telegram://updates",
                "name": "Recent updates",
                "mimeType": "application/json",
                "description": "Recent new/edit message events across allowed chats.",
            },
        ]
        for dialog in dialogs:
            peer_id = utils.get_peer_id(dialog.entity)
            resources.append(
                {
                    "uri": f"telegram://chat/{peer_id}",
                    "name": dialog.title,
                    "mimeType": "application/json",
                    "description": f"Recent messages for {dialog.title}.",
                }
            )
        if self.whatsapp is not None:
            resources.extend(
                [
                    {
                        "uri": "whatsapp://config",
                        "name": "WhatsApp configuration",
                        "mimeType": "application/json",
                        "description": "Status and Cloud-label scope for the local WhatsApp bridge.",
                    },
                    {
                        "uri": "whatsapp://chats",
                        "name": "WhatsApp chats",
                        "mimeType": "application/json",
                        "description": "WhatsApp chats currently carrying the Cloud label.",
                    },
                    {
                        "uri": "whatsapp://updates",
                        "name": "WhatsApp updates",
                        "mimeType": "application/json",
                        "description": "Recent WhatsApp message events across allowed chats.",
                    },
                ]
            )
            try:
                status = await self.whatsapp.get_status(limit=500)
            except WhatsAppBridgeError:
                status = {"chats": []}
            for chat in status.get("chats", []):
                if not isinstance(chat, dict):
                    continue
                jid = str(chat.get("jid") or "")
                if not jid:
                    continue
                resources.append(
                    {
                        "uri": f"whatsapp://chat/{quote(jid, safe='@.-_')}",
                        "name": str(chat.get("title") or jid),
                        "mimeType": "application/json",
                        "description": f"Recent WhatsApp messages for {chat.get('title') or jid}.",
                    }
                )
        if self._imessage_enabled():
            resources.extend(
                [
                    {
                        "uri": "imessage://config",
                        "name": "iMessage configuration",
                        "mimeType": "application/json",
                        "description": "Local Messages app status, automation access, and history database state.",
                    },
                    {
                        "uri": "imessage://chats",
                        "name": "iMessage chats",
                        "mimeType": "application/json",
                        "description": "iMessage chats currently visible through the local Messages app.",
                    },
                    {
                        "uri": "imessage://updates",
                        "name": "iMessage updates",
                        "mimeType": "application/json",
                        "description": "Recent iMessage message events from the local Messages database.",
                    },
                ]
            )
            try:
                status = await self.imessage.get_status(limit=500)
            except IMessageBridgeError:
                status = {"chats": []}
            for chat in status.get("chats", []):
                if not isinstance(chat, dict):
                    continue
                chat_id = str(chat.get("chat_id") or "")
                if not chat_id:
                    continue
                resources.append(
                    {
                        "uri": f"imessage://chat/{quote(chat_id, safe='@.-_+;')}",
                        "name": str(chat.get("title") or chat_id),
                        "mimeType": "application/json",
                        "description": f"Recent iMessage messages for {chat.get('title') or chat_id}.",
                    }
                )
        return resources

    async def _read_resource(self, params: dict[str, object]) -> dict[str, object]:
        uri = str(params.get("uri") or "")
        if uri == "telegram://config":
            payload = await self._resource_config()
        elif uri == "telegram://chats":
            payload = await self._tool_list_chats(limit=500)
        elif uri == "telegram://updates":
            payload = await self._tool_get_updates(limit=100)
        elif uri.startswith("telegram://chat/"):
            payload = await self._tool_get_messages(peer=uri.removeprefix("telegram://chat/"), limit=50)
        elif uri == "whatsapp://config":
            payload = await self._resource_whatsapp_config()
        elif uri == "whatsapp://chats":
            payload = await self._tool_whatsapp_list_chats(limit=500)
        elif uri == "whatsapp://updates":
            payload = await self._tool_whatsapp_get_updates(limit=100)
        elif uri.startswith("whatsapp://chat/"):
            payload = await self._tool_whatsapp_get_messages(jid=unquote(uri.removeprefix("whatsapp://chat/")), limit=50)
        elif uri == "imessage://config":
            payload = await self._resource_imessage_config()
        elif uri == "imessage://chats":
            payload = await self._tool_imessage_list_chats(limit=500)
        elif uri == "imessage://updates":
            payload = await self._tool_imessage_get_updates(limit=100)
        elif uri.startswith("imessage://chat/"):
            payload = await self._tool_imessage_get_messages(
                chat_id=unquote(uri.removeprefix("imessage://chat/")),
                limit=50,
            )
        else:
            raise McpHttpError(404, f"Unknown resource: {uri}")

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, ensure_ascii=False, indent=2),
                }
            ]
        }

    async def _tool_list_chats(self, *, limit: int) -> dict[str, object]:
        dialogs = await self.upstream.get_dialogs(limit=limit)
        return {"ok": True, "chats": [self._serialize_dialog(dialog) for dialog in dialogs]}

    async def _tool_get_messages(self, *, peer: object, limit: int) -> dict[str, object]:
        result = await self.upstream.get_history(self._normalize_peer(peer), limit=limit)
        return {
            "ok": True,
            "messages": [self._serialize_message(message) for message in result.messages],
            "chat_count": len(result.chats),
            "user_count": len(result.users),
        }

    async def _tool_search_messages(self, *, query: str, peer: object | None, limit: int) -> dict[str, object]:
        if peer is not None and str(peer).strip():
            result = await self.upstream.search_messages(
                self._normalize_peer(peer),
                query=query,
                filter=types.InputMessagesFilterEmpty(),
                limit=limit,
            )
        else:
            result = await self.upstream.search_all_messages(
                query=query,
                filter=types.InputMessagesFilterEmpty(),
                limit=limit,
            )
        return {
            "ok": True,
            "messages": [self._serialize_message(message) for message in result.messages],
            "chat_count": len(result.chats),
            "user_count": len(result.users),
            "dropped_count": result.dropped_count,
        }

    async def _tool_send_message(self, *, peer: object, text: str, reply_to_message_id: int | None) -> dict[str, object]:
        message = await self.upstream.send_message(self._normalize_peer(peer), text, reply_to=reply_to_message_id)
        return {"ok": True, "message": self._serialize_message(message)}

    async def _tool_delete_messages(self, *, peer: object, message_ids: list[int]) -> dict[str, object]:
        result = await self.upstream.delete_messages(self._normalize_peer(peer), message_ids, revoke=True)
        return {"ok": True, "pts": getattr(result, "pts", None), "pts_count": getattr(result, "pts_count", None)}

    async def _tool_mark_read(self, *, peer: object, max_id: int) -> dict[str, object]:
        if max_id > 0:
            await self.upstream.read_history(self._normalize_peer(peer), max_id)
        else:
            await self.upstream.mark_read(self._normalize_peer(peer))
        return {"ok": True}

    async def _tool_list_members(self, *, peer: object, limit: int) -> dict[str, object]:
        participants = await self.upstream.list_participants(self._normalize_peer(peer), limit=limit)
        return {"ok": True, "participants": [self._serialize_user(user) for user in participants]}

    async def _tool_get_updates(self, *, limit: int) -> dict[str, object]:
        updates = list(self._recent_updates)[-limit:]
        return {"ok": True, "updates": updates}

    async def _tool_whatsapp_list_chats(self, *, limit: int) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        payload = await self.whatsapp.get_chats(limit=limit)
        return {"ok": True, "chats": payload.get("chats", [])}

    async def _tool_whatsapp_get_auth_status(self) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        return await self.whatsapp.get_status(limit=50)

    async def _tool_whatsapp_get_messages(self, *, jid: str, limit: int) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        payload = await self.whatsapp.get_chat(jid, limit=limit)
        return {
            "ok": True,
            "chat": payload.get("chat"),
            "messages": payload.get("messages", []),
        }

    async def _tool_whatsapp_send_message(self, *, jid: str, text: str) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        payload = await self.whatsapp.send_message(jid=jid, text=text)
        return {"ok": True, "message": payload.get("message")}

    async def _tool_whatsapp_mark_read(self, *, jid: str, message_id: str | None) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        return await self.whatsapp.mark_read(jid=jid, message_id=message_id)

    async def _tool_whatsapp_get_updates(self, *, limit: int) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        payload = await self.whatsapp.get_updates(limit=limit)
        return {"ok": True, "updates": payload.get("updates", [])}

    async def _tool_imessage_list_chats(self, *, limit: int) -> dict[str, object]:
        if not self.config.imessage_enabled:
            raise McpHttpError(404, "Messages integration is disabled")
        if self.imessage is None:
            raise McpHttpError(404, "iMessage bridge is unavailable")
        payload = await self.imessage.get_chats(limit=limit)
        return {"ok": True, "chats": payload.get("chats", [])}

    async def _tool_imessage_get_auth_status(self) -> dict[str, object]:
        if not self.config.imessage_enabled:
            raise McpHttpError(404, "Messages integration is disabled")
        if self.imessage is None:
            raise McpHttpError(404, "iMessage bridge is unavailable")
        return await self.imessage.get_status(limit=50)

    async def _tool_imessage_get_messages(self, *, chat_id: str, limit: int) -> dict[str, object]:
        if not self.config.imessage_enabled:
            raise McpHttpError(404, "Messages integration is disabled")
        if self.imessage is None:
            raise McpHttpError(404, "iMessage bridge is unavailable")
        payload = await self.imessage.get_chat(chat_id, limit=limit)
        return {"ok": True, "chat": payload.get("chat"), "messages": payload.get("messages", [])}

    async def _tool_imessage_send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
        if not self.config.imessage_enabled:
            raise McpHttpError(404, "Messages integration is disabled")
        if self.imessage is None:
            raise McpHttpError(404, "iMessage bridge is unavailable")
        payload = await self.imessage.send_message(chat_id=chat_id, text=text)
        return {"ok": True, "message": payload.get("message")}

    async def _tool_imessage_get_updates(self, *, limit: int) -> dict[str, object]:
        if not self.config.imessage_enabled:
            raise McpHttpError(404, "Messages integration is disabled")
        if self.imessage is None:
            raise McpHttpError(404, "iMessage bridge is unavailable")
        payload = await self.imessage.get_updates(limit=limit)
        return {"ok": True, "updates": payload.get("updates", [])}

    async def _resource_config(self) -> dict[str, object]:
        identity = await self.upstream.get_identity()
        whatsapp = None
        if self.whatsapp is not None:
            try:
                whatsapp = await self.whatsapp.get_status(limit=50)
            except WhatsAppBridgeError as exc:
                whatsapp = {"ok": False, "last_error": str(exc)}
        imessage = None
        if self._imessage_enabled():
            try:
                imessage = await self.imessage.get_status(limit=50)
            except IMessageBridgeError as exc:
                imessage = {"ok": False, "last_error": str(exc)}
        elif self.imessage is not None:
            imessage = {"ok": True, "enabled": False, "available": False, "last_error": None}
        return {
            "ok": True,
            "upstream": identity,
            "mcp": {
                "host": self.config.mcp_host,
                "port": self.config.mcp_port,
                "path": self.config.mcp_path,
                "auth": "Bearer token",
                "session_header": "Mcp-Session-Id",
                "sse_get_supported": True,
            },
            "mtproto": {
                "host": self.config.downstream_host,
                "port": self.config.mtproto_port,
                "api_id": self.config.downstream_api_id,
            },
            "whatsapp": whatsapp,
            "imessage": imessage,
        }

    async def _resource_whatsapp_config(self) -> dict[str, object]:
        if self.whatsapp is None:
            raise McpHttpError(404, "WhatsApp bridge is unavailable")
        payload = await self.whatsapp.get_status(limit=200)
        return {
            "ok": True,
            "bridge": payload,
            "mcp": {
                "host": self.config.mcp_host,
                "port": self.config.mcp_port,
                "path": self.config.mcp_path,
            },
        }

    async def _resource_imessage_config(self) -> dict[str, object]:
        if not self.config.imessage_enabled:
            raise McpHttpError(404, "Messages integration is disabled")
        if self.imessage is None:
            raise McpHttpError(404, "iMessage bridge is unavailable")
        payload = await self.imessage.get_status(limit=200)
        return {
            "ok": True,
            "bridge": payload,
            "mcp": {
                "host": self.config.mcp_host,
                "port": self.config.mcp_port,
                "path": self.config.mcp_path,
            },
        }

    def _tools(self) -> list[dict[str, object]]:
        tools = [
            {
                "name": "telegram.list_chats",
                "description": "List chats visible through the Cloud folder policy.",
                "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}},
            },
            {
                "name": "telegram.get_messages",
                "description": "Get recent messages from one allowed chat.",
                "inputSchema": {
                    "type": "object",
                    "required": ["peer"],
                    "properties": {
                        "peer": {"type": "string", "description": "Telegram peer id, username, or @username."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                },
            },
            {
                "name": "telegram.search_messages",
                "description": "Search messages inside one allowed chat, or across all Cloud chats if peer is omitted.",
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "peer": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    },
                },
            },
            {
                "name": "telegram.send_message",
                "description": "Send a message into one allowed chat. Set reply_to_message_id to reply.",
                "inputSchema": {
                    "type": "object",
                    "required": ["peer", "text"],
                    "properties": {
                        "peer": {"type": "string"},
                        "text": {"type": "string"},
                        "reply_to_message_id": {"type": "integer"},
                    },
                },
            },
            {
                "name": "telegram.delete_messages",
                "description": "Delete one or more messages in an allowed chat.",
                "inputSchema": {
                    "type": "object",
                    "required": ["peer", "message_ids"],
                    "properties": {
                        "peer": {"type": "string"},
                        "message_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                    },
                },
            },
            {
                "name": "telegram.mark_read",
                "description": "Mark an allowed chat as read, optionally up to max_id.",
                "inputSchema": {
                    "type": "object",
                    "required": ["peer"],
                    "properties": {
                        "peer": {"type": "string"},
                        "max_id": {"type": "integer"},
                    },
                },
            },
            {
                "name": "telegram.list_members",
                "description": "List members of one allowed group or channel.",
                "inputSchema": {
                    "type": "object",
                    "required": ["peer"],
                    "properties": {
                        "peer": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    },
                },
            },
            {
                "name": "telegram.get_updates",
                "description": "Fetch recent update events. For push, subscribe to telegram://updates or telegram://chat/<peer_id> over SSE.",
                "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}},
            },
            {
                "name": "whatsapp.list_chats",
                "description": "List WhatsApp chats currently carrying the Cloud label.",
                "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}},
            },
            {
                "name": "whatsapp.get_auth_status",
                "description": "Get WhatsApp bridge connection state, including any pending QR login payload.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "whatsapp.get_messages",
                "description": "Get recent messages from one allowed WhatsApp chat.",
                "inputSchema": {
                    "type": "object",
                    "required": ["jid"],
                    "properties": {
                        "jid": {"type": "string", "description": "WhatsApp chat JID, for example 12345@s.whatsapp.net or 123-456@g.us."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                },
            },
            {
                "name": "whatsapp.send_message",
                "description": "Send a text message into one allowed WhatsApp chat.",
                "inputSchema": {
                    "type": "object",
                    "required": ["jid", "text"],
                    "properties": {
                        "jid": {"type": "string"},
                        "text": {"type": "string"},
                    },
                },
            },
            {
                "name": "whatsapp.mark_read",
                "description": "Mark an allowed WhatsApp chat as read, optionally targeting a specific message id.",
                "inputSchema": {
                    "type": "object",
                    "required": ["jid"],
                    "properties": {
                        "jid": {"type": "string"},
                        "message_id": {"type": "string"},
                    },
                },
            },
            {
                "name": "whatsapp.get_updates",
                "description": "Fetch recent WhatsApp message events from chats carrying the Cloud label.",
                "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}},
            },
        ]
        if self._imessage_enabled():
            tools.extend(
                [
                    {
                        "name": "imessage.list_chats",
                        "description": "List local iMessage chats visible in the Messages app.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                            },
                        },
                    },
                    {
                        "name": "imessage.get_auth_status",
                        "description": "Get local iMessage integration status, including Messages automation and chat.db access.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "imessage.get_messages",
                        "description": "Get recent messages from one local iMessage chat.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["chat_id"],
                            "properties": {
                                "chat_id": {
                                    "type": "string",
                                    "description": "Messages chat identifier such as any;-;+15551234567 or any;+;chat...",
                                },
                                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                            },
                        },
                    },
                    {
                        "name": "imessage.send_message",
                        "description": "Send a text message into one local iMessage chat via the Messages app.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["chat_id", "text"],
                            "properties": {
                                "chat_id": {"type": "string"},
                                "text": {"type": "string"},
                            },
                        },
                    },
                    {
                        "name": "imessage.get_updates",
                        "description": "Fetch recent iMessage message events from the local Messages database.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                            },
                        },
                    },
                ]
            )
        return tools

    async def _fan_out_updates(self) -> None:
        assert self._update_queue is not None
        while True:
            envelope = await self._update_queue.get()
            payload = self._serialize_update(envelope)
            self._recent_updates.append(payload)
            uris = {"telegram://updates"}
            if envelope.peer_id is not None:
                uris.add(f"telegram://chat/{envelope.peer_id}")
            for session in list(self._sessions.values()):
                if session.writer is None:
                    continue
                if not session.subscriptions.intersection(uris):
                    continue
                for uri in sorted(session.subscriptions.intersection(uris)):
                    await self._send_notification(
                        session,
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/resources/updated",
                            "params": {"uri": uri},
                        },
                    )

    async def _open_event_stream(self, session: McpSession, writer: asyncio.StreamWriter) -> None:
        session.writer = writer
        headers = [
            "HTTP/1.1 200 OK",
            "Content-Type: text/event-stream",
            "Cache-Control: no-store",
            "Connection: keep-alive",
            "",
            "",
        ]
        writer.write("\r\n".join(headers).encode("ascii"))
        writer.write(b": connected\n\n")
        await writer.drain()
        try:
            while not writer.is_closing():
                await asyncio.sleep(15)
                writer.write(b": keepalive\n\n")
                await writer.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            raise
        finally:
            session.writer = None

    async def _send_notification(self, session: McpSession, payload: dict[str, object]) -> None:
        if session.writer is None:
            return
        body = json.dumps(payload, ensure_ascii=False)
        async with session.lock:
            try:
                session.writer.write(f"data: {body}\n\n".encode("utf-8"))
                await session.writer.drain()
            except (ConnectionError, OSError):
                session.writer = None

    def _serialize_dialog(self, dialog) -> dict[str, object]:
        entity = dialog.entity
        return {
            "peer_id": str(utils.get_peer_id(entity)),
            "title": dialog.title,
            "username": getattr(entity, "username", None),
            "kind": self._dialog_kind(dialog),
        }

    def _serialize_message(self, message) -> dict[str, object]:
        peer = getattr(message, "peer_id", None)
        sender = getattr(message, "from_id", None)
        return {
            "id": getattr(message, "id", None),
            "peer_id": str(utils.get_peer_id(peer)) if peer is not None else None,
            "sender_id": str(utils.get_peer_id(sender)) if sender is not None else None,
            "text": getattr(message, "message", None),
            "date": getattr(message, "date", None).isoformat() if getattr(message, "date", None) else None,
            "out": bool(getattr(message, "out", False)),
            "media": type(getattr(message, "media", None)).__name__ if getattr(message, "media", None) is not None else None,
        }

    def _serialize_user(self, user) -> dict[str, object]:
        return {
            "id": str(utils.get_peer_id(types.PeerUser(user.id))),
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }

    def _serialize_update(self, envelope: UpdateEnvelope) -> dict[str, object]:
        return envelope.to_dict(self._serialize_message)

    def _dialog_kind(self, dialog) -> str:
        if getattr(dialog, "is_user", False):
            return "dm"
        if getattr(dialog, "is_group", False):
            return "group"
        if getattr(dialog, "is_channel", False):
            return "channel"
        return "chat"

    def _normalize_peer(self, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("@"):
                return text[1:]
            if text.lstrip("-").isdigit():
                return int(text)
            return text
        return value

    def _require_peer(self, arguments: dict[str, object]) -> object:
        peer = arguments.get("peer")
        if peer is None or not str(peer).strip():
            raise McpHttpError(400, "Missing required tool argument: peer")
        return peer

    def _require_message_ids(self, arguments: dict[str, object]) -> list[int]:
        value = arguments.get("message_ids")
        if not isinstance(value, list) or not value:
            raise McpHttpError(400, "Missing required tool argument: message_ids")
        return [int(item) for item in value]

    def _require_whatsapp_jid(self, arguments: dict[str, object]) -> str:
        jid = str(arguments.get("jid") or "").strip()
        if not jid:
            raise McpHttpError(400, "Missing required tool argument: jid")
        return jid

    def _require_imessage_chat_id(self, arguments: dict[str, object]) -> str:
        chat_id = str(arguments.get("chat_id") or "").strip()
        if not chat_id:
            raise McpHttpError(400, "Missing required tool argument: chat_id")
        return chat_id

    def _optional_int(self, value: object) -> int | None:
        if value in (None, "", False):
            return None
        return int(value)

    def _optional_str(self, value: object) -> str | None:
        if value in (None, "", False):
            return None
        return str(value)

    def _validate_resource_uri(self, uri: str) -> None:
        if uri == "telegram://config" or uri == "telegram://chats" or uri == "telegram://updates":
            return
        if uri.startswith("telegram://chat/"):
            return
        if uri == "whatsapp://config" or uri == "whatsapp://chats" or uri == "whatsapp://updates":
            return
        if uri.startswith("whatsapp://chat/"):
            return
        if uri == "imessage://config" or uri == "imessage://chats" or uri == "imessage://updates":
            return
        if uri.startswith("imessage://chat/"):
            return
        raise McpHttpError(404, f"Unknown resource: {uri}")

    def _validate_auth(self, headers: dict[str, str]) -> None:
        expected = f"Bearer {self.config.mcp_token}"
        if headers.get("authorization", "") != expected:
            raise McpHttpError(401, "Unauthorized")

    def _validate_origin(self, headers: dict[str, str]) -> None:
        origin = headers.get("origin", "")
        if not origin:
            return
        allowed = (
            origin.startswith("http://localhost")
            or origin.startswith("http://127.0.0.1")
            or origin.startswith("https://localhost")
            or origin.startswith("https://127.0.0.1")
        )
        if not allowed:
            raise McpHttpError(403, "Forbidden origin")

    def _create_session(self) -> McpSession:
        session_id = secrets.token_urlsafe(18)
        session = McpSession(
            session_id=session_id,
            created_at=datetime.now(timezone.utc),
            protocol_version=SERVER_PROTOCOL_VERSION,
        )
        self._sessions[session_id] = session
        return session

    def _require_session(self, headers: dict[str, str]) -> McpSession:
        session_id = headers.get(SESSION_HEADER, "")
        if not session_id:
            raise McpHttpError(400, "Missing Mcp-Session-Id header")
        session = self._sessions.get(session_id)
        if session is None:
            raise McpHttpError(404, "Unknown MCP session")
        return session

    async def _read_headers(self, reader: asyncio.StreamReader) -> dict[str, str]:
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line or line in {b"\r\n", b"\n"}:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if ":" not in text:
                continue
            name, value = text.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        return headers

    async def _write_json_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        payload: dict[str, object],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        reason = {
            200: "OK",
            202: "Accepted",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
        }.get(status, "OK")
        headers = [
            f"HTTP/1.1 {status} {reason}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(body)}",
            "Cache-Control: no-store",
            "Connection: close",
        ]
        for key, value in (extra_headers or {}).items():
            headers.append(f"{key}: {value}")
        headers.extend(["", ""])
        writer.write("\r\n".join(headers).encode("ascii") + body)
        await writer.drain()

    def _result(self, rpc_id: object, result: dict[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def _error(self, rpc_id: object, code: int, message: str) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
