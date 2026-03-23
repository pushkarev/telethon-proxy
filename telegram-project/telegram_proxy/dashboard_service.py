from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from telethon import utils

from .config import ProxyConfig
from .downstream_registry import DownstreamRegistry
from .mtproto_service import MTProtoProxyServer
from .upstream import UpstreamAdapter, UpstreamUnavailableError


SUPPORTED_APIS = {
    "forwarded": [
        "contacts.resolveUsername",
        "messages.getDialogs",
        "messages.getPeerDialogs",
        "messages.getFullChat",
        "messages.getHistory",
        "messages.search",
        "messages.searchGlobal (Cloud-scoped local merge)",
        "messages.sendMessage",
        "messages.sendMedia",
        "messages.readHistory",
        "messages.deleteMessages",
        "channels.deleteMessages",
        "channels.getParticipants",
        "updates.getDifference",
    ],
    "proxy_local": [
        "auth.sendCode",
        "auth.signIn",
        "help.getConfig",
        "users.getUsers(self)",
        "updates.getState",
        "upload.saveFilePart",
        "upload.saveBigFilePart",
    ],
}


WEBUI_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "webui"
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


class ProxyDashboardServer:
    def __init__(
        self,
        config: ProxyConfig,
        upstream: UpstreamAdapter,
        registry: DownstreamRegistry,
        mtproto: MTProtoProxyServer,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.registry = registry
        self.mtproto = mtproto
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.dashboard_host,
            port=self.config.dashboard_port,
        )
        if self._server.sockets:
            self.config.dashboard_port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, target, _version = request_line.decode("ascii", errors="replace").strip().split(" ", 2)
            while True:
                line = await reader.readline()
                if not line or line in {b"\r\n", b"\n"}:
                    break

            if method != "GET":
                await self._write_response(writer, 405, b"Method Not Allowed", "text/plain; charset=utf-8")
                return

            url = urlsplit(target)
            if url.path == "/" or url.path == "/index.html":
                await self._serve_static(writer, "index.html")
                return
            if url.path in {"/styles.css", "/app.js"}:
                await self._serve_static(writer, url.path.lstrip("/"))
                return
            if url.path == "/api/overview":
                payload = await self._build_overview()
                await self._write_json(writer, 200, payload)
                return
            if url.path == "/api/chat":
                params = parse_qs(url.query)
                peer_id = int(params.get("peer_id", ["0"])[0])
                payload = await self._build_chat(peer_id)
                await self._write_json(writer, 200, payload)
                return

            await self._write_response(writer, 404, b"Not Found", "text/plain; charset=utf-8")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _serve_static(self, writer: asyncio.StreamWriter, asset_name: str) -> None:
        path = (WEBUI_DIR / asset_name).resolve()
        if path.parent != WEBUI_DIR or not path.exists() or not path.is_file():
            await self._write_response(writer, 404, b"Not Found", "text/plain; charset=utf-8")
            return
        content_type = STATIC_CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        await self._write_response(writer, 200, path.read_bytes(), content_type)

    async def _build_overview(self) -> dict[str, object]:
        chats = []
        error = None
        try:
            dialogs = await self.upstream.get_dialogs(limit=500)
            chats = [self._serialize_dialog(dialog) for dialog in dialogs]
        except UpstreamUnavailableError:
            error = "Upstream Telegram connection is currently unavailable."

        issued_clients = self.registry.list_clients()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            "config": {
                "cloud_folder_name": self.config.cloud_folder_name,
                "mtproto_port": self.config.mtproto_port,
                "downstream_host": self.config.downstream_host,
                "downstream_api_id": self.config.downstream_api_id,
                "downstream_session_label": self.config.downstream_session_label,
                "dashboard_host": self.config.dashboard_host,
                "dashboard_port": self.config.dashboard_port,
                "allow_member_listing": self.config.allow_member_listing,
                "issued_client_count": len(issued_clients),
                "downstream_api_hash": self.config.downstream_api_hash,
                "downstream_login_phone": "+15550000000",
                "downstream_login_code": self.config.downstream_login_code,
                "upstream_reconnect_min_delay": self.config.upstream_reconnect_min_delay,
                "upstream_reconnect_max_delay": self.config.upstream_reconnect_max_delay,
            },
            "upstream": await self._load_upstream_identity(error is None),
            "clients": self.mtproto.active_connections_snapshot(),
            "downstream_credentials": [self._serialize_credential(client) for client in issued_clients],
            "mcp": {
                "host": self.config.mcp_host,
                "port": self.config.mcp_port,
                "path": self.config.mcp_path,
                "transport": "HTTP JSON-RPC",
                "auth": "Authorization: Bearer <token>",
                "allowed_origin": "localhost / 127.0.0.1",
                "token": self.config.mcp_token,
            },
            "chats": chats,
            "apis": SUPPORTED_APIS,
        }

    async def _build_chat(self, peer_id: int) -> dict[str, object]:
        dialogs = await self.upstream.get_dialogs(limit=500)
        selected = next((dialog for dialog in dialogs if utils.get_peer_id(dialog.entity) == peer_id), None)
        if selected is None:
            return {"chat": None, "messages": []}
        history = await self.upstream.get_history(peer_id, limit=50)
        return {
            "chat": self._serialize_dialog(selected),
            "messages": [self._serialize_message(message) for message in history.messages],
        }

    async def _write_json(self, writer: asyncio.StreamWriter, status: int, payload: dict[str, object]) -> None:
        await self._write_response(
            writer,
            status,
            json.dumps(payload, ensure_ascii=True).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        reason = {
            200: "OK",
            404: "Not Found",
            405: "Method Not Allowed",
        }.get(status, "OK")
        headers = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Cache-Control: no-store",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(headers).encode("ascii") + body)
        await writer.drain()

    def _serialize_dialog(self, dialog) -> dict[str, object]:
        entity = dialog.entity
        return {
            "peer_id": utils.get_peer_id(entity),
            "title": dialog.title,
            "username": getattr(entity, "username", None),
            "kind": self._dialog_kind(dialog),
        }

    def _serialize_message(self, message) -> dict[str, object]:
        media = getattr(message, "media", None)
        media_kind = media.__class__.__name__ if media is not None else None
        return {
            "id": getattr(message, "id", None),
            "text": getattr(message, "message", None),
            "date": getattr(message, "date", None).isoformat() if getattr(message, "date", None) else None,
            "out": bool(getattr(message, "out", False)),
            "media": media_kind,
        }

    def _dialog_kind(self, dialog) -> str:
        if getattr(dialog, "is_user", False):
            return "dm"
        if getattr(dialog, "is_group", False):
            return "group"
        if getattr(dialog, "is_channel", False):
            return "channel"
        return "chat"

    async def _load_upstream_identity(self, enabled: bool) -> dict[str, object]:
        if not enabled:
            return {"name": "Unavailable", "phone": None, "username": None}
        try:
            return await self.upstream.get_identity()
        except Exception:
            return {"name": "Unavailable", "phone": None, "username": None}

    def _serialize_credential(self, client) -> dict[str, object]:
        return {
            "label": client.label,
            "created_at": client.created_at.isoformat(),
            "host": client.host,
            "port": client.port,
            "phone": client.phone,
            "session_string": client.session_string,
        }
