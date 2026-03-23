from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from telethon import utils

from .config import ProxyConfig
from .downstream_registry import DownstreamRegistry
from .mcp_service import McpServer
from .mtproto_service import MTProtoProxyServer
from .secrets_store import MacOSSecretStore
from .upstream import UpstreamAdapter, UpstreamUnavailableError
from .whatsapp_bridge import WhatsAppBridge, WhatsAppBridgeError


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

class ProxyDashboardServer:
    def __init__(
        self,
        config: ProxyConfig,
        upstream: UpstreamAdapter,
        registry: DownstreamRegistry,
        mtproto: MTProtoProxyServer,
        mcp: McpServer,
        telegram_auth=None,
        whatsapp: WhatsAppBridge | None = None,
        secret_store: MacOSSecretStore | None = None,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.registry = registry
        self.mtproto = mtproto
        self.mcp = mcp
        self.telegram_auth = telegram_auth
        self.whatsapp = whatsapp
        self.secret_store = secret_store or MacOSSecretStore()
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
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if not line or line in {b"\r\n", b"\n"}:
                    break
                text = line.decode("ascii", errors="replace").strip()
                if ":" not in text:
                    continue
                name, value = text.split(":", 1)
                headers[name.strip().lower()] = value.strip()

            body = b""
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length:
                body = await reader.readexactly(content_length)

            url = urlsplit(target)
            if method == "GET" and url.path == "/api/overview":
                payload = await self._build_overview()
                await self._write_json(writer, 200, payload)
                return
            if method == "GET" and url.path == "/api/chat":
                params = parse_qs(url.query)
                peer_id = int(params.get("peer_id", ["0"])[0])
                payload = await self._build_chat(peer_id)
                await self._write_json(writer, 200, payload)
                return
            if method == "GET" and url.path == "/api/telegram/auth":
                await self._write_json(writer, 200, await self._build_telegram_auth())
                return
            if method == "GET" and url.path == "/api/whatsapp/auth":
                await self._write_json(writer, 200, await self._build_whatsapp_auth())
                return
            if method == "GET" and url.path == "/api/whatsapp/chat":
                params = parse_qs(url.query)
                jid = params.get("jid", [""])[0]
                await self._write_json(writer, 200, await self._build_whatsapp_chat(jid))
                return
            if method == "GET" and url.path == "/api/mcp/token":
                await self._write_json(writer, 200, await self._build_mcp_token())
                return
            if method == "POST" and url.path.startswith("/api/telegram/auth/"):
                await self._handle_telegram_auth_post(writer, url.path, body)
                return
            if method == "POST" and url.path.startswith("/api/whatsapp/auth/"):
                await self._handle_whatsapp_auth_post(writer, url.path, body)
                return
            if method == "POST" and url.path == "/api/mtproto/enabled":
                await self._handle_mtproto_enabled_post(writer, body)
                return
            if method == "POST" and url.path == "/api/mcp/config":
                await self._handle_mcp_config_post(writer, body)
                return
            if method == "POST" and url.path == "/api/mcp/token/rotate":
                await self._handle_mcp_token_rotate(writer)
                return
            if method != "GET" and method != "POST":
                await self._write_response(writer, 405, b"Method Not Allowed", "text/plain; charset=utf-8")
                return

            await self._write_response(writer, 404, b"Not Found", "text/plain; charset=utf-8")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _build_overview(self) -> dict[str, object]:
        chats = []
        error = None
        try:
            dialogs = await self.upstream.get_dialogs(limit=500)
            chats = [self._serialize_dialog(dialog) for dialog in dialogs]
        except (UpstreamUnavailableError, RuntimeError):
            error = "Upstream Telegram connection is not available yet. Use Telegram -> Settings to authorize."

        issued_clients = self.registry.list_clients()
        whatsapp = await self._build_whatsapp_auth()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            "config": {
                "cloud_folder_name": self.config.cloud_folder_name,
                "whatsapp_cloud_label_name": self.config.whatsapp_cloud_label_name,
                "mtproto_enabled": self.config.mtproto_enabled,
                "mtproto_listening": self.mtproto.is_running,
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
                "listening": self.mcp.is_running,
                "transport": "HTTP JSON-RPC",
                "auth": "Authorization: Bearer <token>",
                "allowed_origin": "localhost / 127.0.0.1",
                "token_hidden": True,
                "token_env_managed": self.config.mcp_token_env_managed,
                "bind_options": self._mcp_bind_options(),
            },
            "telegram_auth": await self._build_telegram_auth(),
            "whatsapp": whatsapp,
            "chats": chats,
            "apis": SUPPORTED_APIS,
        }

    async def _build_chat(self, peer_id: int) -> dict[str, object]:
        try:
            dialogs = await self.upstream.get_dialogs(limit=500)
        except (UpstreamUnavailableError, RuntimeError):
            return {"chat": None, "messages": []}
        selected = next((dialog for dialog in dialogs if utils.get_peer_id(dialog.entity) == peer_id), None)
        if selected is None:
            return {"chat": None, "messages": []}
        history = await self.upstream.get_history(peer_id, limit=50)
        return {
            "chat": self._serialize_dialog(selected),
            "messages": [self._serialize_message(message) for message in history.messages],
        }

    async def _build_whatsapp_auth(self) -> dict[str, object]:
        if self.whatsapp is None:
            return {
                "ok": False,
                "available": False,
                "connected": False,
                "has_session": False,
                "cloud_label_name": self.config.whatsapp_cloud_label_name,
                "chats": [],
                "last_error": "WhatsApp bridge is unavailable",
            }
        try:
            payload = await self.whatsapp.get_status(limit=500)
        except WhatsAppBridgeError as exc:
            return {
                "ok": False,
                "available": False,
                "connected": False,
                "has_session": False,
                "cloud_label_name": self.config.whatsapp_cloud_label_name,
                "chats": [],
                "last_error": str(exc),
            }
        payload["available"] = True
        return payload

    async def _build_whatsapp_chat(self, jid: str) -> dict[str, object]:
        if self.whatsapp is None:
            return {"chat": None, "messages": [], "error": "WhatsApp bridge is unavailable"}
        try:
            payload = await self.whatsapp.get_chat(jid, limit=80)
        except WhatsAppBridgeError as exc:
            return {"chat": None, "messages": [], "error": str(exc)}
        return payload

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
            400: "Bad Request",
            200: "OK",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
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

    async def _build_telegram_auth(self) -> dict[str, object]:
        if self.telegram_auth is None:
            return {
                "keychain_backend": "Unavailable",
                "has_api_credentials": False,
                "has_session": False,
                "phone": self.config.upstream_phone,
                "saved_phone": self.config.upstream_phone or None,
                "next_step": "credentials",
                "pending_phone": None,
                "last_error": None,
            }
        return await self.telegram_auth.get_status()

    async def _handle_telegram_auth_post(
        self,
        writer: asyncio.StreamWriter,
        path: str,
        body: bytes,
    ) -> None:
        if self.telegram_auth is None:
            await self._write_json(writer, 500, {"error": "Telegram auth service is unavailable"})
            return
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            await self._write_json(writer, 400, {"error": "Expected a JSON request body"})
            return

        try:
            if path == "/api/telegram/auth/save":
                result = await self.telegram_auth.save_credentials(
                    api_id=str(payload.get("api_id", "")),
                    api_hash=str(payload.get("api_hash", "")),
                    phone=str(payload.get("phone", "")),
                )
            elif path == "/api/telegram/auth/request-code":
                result = await self.telegram_auth.request_code(phone=str(payload.get("phone", "")))
            elif path == "/api/telegram/auth/submit-code":
                result = await self.telegram_auth.submit_code(code=str(payload.get("code", "")))
            elif path == "/api/telegram/auth/submit-password":
                result = await self.telegram_auth.submit_password(password=str(payload.get("password", "")))
            elif path == "/api/telegram/auth/clear-session":
                result = await self.telegram_auth.clear_saved_session()
            elif path == "/api/telegram/auth/clear":
                result = await self.telegram_auth.clear_saved_auth()
            else:
                await self._write_response(writer, 404, b"Not Found", "text/plain; charset=utf-8")
                return
        except ValueError as exc:
            await self._write_json(writer, 400, {"error": str(exc)})
            return
        except Exception as exc:
            await self._write_json(writer, 500, {"error": str(exc) or exc.__class__.__name__})
            return

        await self._write_json(writer, 200, result)

    async def _handle_mtproto_enabled_post(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            await self._write_json(writer, 400, {"error": "Expected a JSON request body"})
            return

        enabled = bool(payload.get("enabled"))
        if enabled == self.config.mtproto_enabled and self.mtproto.is_running == enabled:
            await self._write_json(
                writer,
                200,
                {
                    "ok": True,
                    "enabled": enabled,
                    "listening": self.mtproto.is_running,
                    "message": "MTProto proxy already matches the requested state.",
                },
            )
            return

        try:
            if enabled:
                await self.mtproto.start()
            else:
                await self.mtproto.stop()
        except Exception as exc:
            await self._write_json(writer, 500, {"error": str(exc) or exc.__class__.__name__})
            return

        self.config.mtproto_enabled = enabled
        await self._write_json(
            writer,
            200,
            {
                "ok": True,
                "enabled": enabled,
                "listening": self.mtproto.is_running,
                "message": "MTProto proxy enabled." if enabled else "MTProto proxy disabled.",
            },
        )

    async def _handle_whatsapp_auth_post(
        self,
        writer: asyncio.StreamWriter,
        path: str,
        body: bytes,
    ) -> None:
        if self.whatsapp is None:
            await self._write_json(writer, 500, {"error": "WhatsApp bridge is unavailable"})
            return
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            await self._write_json(writer, 400, {"error": "Expected a JSON request body"})
            return

        try:
            if path == "/api/whatsapp/auth/request-pairing-code":
                result = await self.whatsapp.request_pairing_code(phone=str(payload.get("phone", "")))
            elif path == "/api/whatsapp/auth/logout":
                result = await self.whatsapp.logout()
            else:
                await self._write_response(writer, 404, b"Not Found", "text/plain; charset=utf-8")
                return
        except WhatsAppBridgeError as exc:
            await self._write_json(writer, 500, {"error": str(exc)})
            return

        await self._write_json(writer, 200, result)

    async def _build_mcp_token(self) -> dict[str, object]:
        return {
            "token": self.config.mcp_token,
            "env_managed": self.config.mcp_token_env_managed,
        }

    async def _handle_mcp_token_rotate(self, writer: asyncio.StreamWriter) -> None:
        if self.config.mcp_token_env_managed:
            await self._write_json(
                writer,
                400,
                {"error": "MCP token is managed by TP_MCP_TOKEN and cannot be rotated from the UI"},
            )
            return
        token = self.secret_store.rotate_mcp_token()
        self.config.mcp_token = token
        await self._write_json(
            writer,
            200,
            {
                "token": token,
                "env_managed": False,
                "message": "MCP bearer token rotated.",
            },
        )

    async def _handle_mcp_config_post(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            await self._write_json(writer, 400, {"error": "Expected a JSON request body"})
            return

        host = str(payload.get("host", "")).strip()
        if not host:
            await self._write_json(writer, 400, {"error": "MCP host is required"})
            return
        try:
            port = int(payload.get("port", 0))
        except (TypeError, ValueError):
            await self._write_json(writer, 400, {"error": "MCP port must be a number"})
            return
        if not 1 <= port <= 65535:
            await self._write_json(writer, 400, {"error": "MCP port must be between 1 and 65535"})
            return

        if host == self.config.mcp_host and port == self.config.mcp_port and self.mcp.is_running:
            await self._write_json(
                writer,
                200,
                {
                    "ok": True,
                    "host": self.config.mcp_host,
                    "port": self.config.mcp_port,
                    "path": self.config.mcp_path,
                    "listening": self.mcp.is_running,
                    "message": "MCP listener already matches the requested interface and port.",
                },
            )
            return

        previous_host = self.config.mcp_host
        previous_port = self.config.mcp_port
        was_running = self.mcp.is_running

        try:
            if was_running:
                await self.mcp.stop()
            self.config.mcp_host = host
            self.config.mcp_port = port
            self.config.save_mcp_settings()
            await self.mcp.start()
        except Exception as exc:
            self.config.mcp_host = previous_host
            self.config.mcp_port = previous_port
            self.config.save_mcp_settings()
            try:
                if self.mcp.is_running:
                    await self.mcp.stop()
            except Exception:
                pass
            if was_running:
                try:
                    await self.mcp.start()
                except Exception:
                    pass
            await self._write_json(writer, 500, {"error": str(exc) or exc.__class__.__name__})
            return

        await self._write_json(
            writer,
            200,
            {
                "ok": True,
                "host": self.config.mcp_host,
                "port": self.config.mcp_port,
                "path": self.config.mcp_path,
                "listening": self.mcp.is_running,
                "message": f"MCP listener moved to {self.config.mcp_host}:{self.config.mcp_port}.",
            },
        )

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

    def _mcp_bind_options(self) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        seen: set[str] = set()

        def add(host: str, label: str, interface: str = "") -> None:
            host = host.strip()
            if not host or host in seen:
                return
            seen.add(host)
            payload = {"host": host, "label": label}
            if interface:
                payload["interface"] = interface
            options.append(payload)

        add("127.0.0.1", "Localhost (lo0)", "lo0")
        add("0.0.0.0", "All interfaces", "*")

        for option in self._detected_interface_options():
            add(option["host"], option["label"], option["interface"])
        add(self.config.downstream_host, f"Advertised host ({self.config.downstream_host})")
        add(self.config.mcp_host, f"Current MCP host ({self.config.mcp_host})")
        return options

    def _detected_interface_options(self) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        for _index, interface in socket.if_nameindex():
            if interface == "lo0":
                continue
            host = self._ipv4_for_interface(interface)
            if not host:
                continue
            options.append(
                {
                    "host": host,
                    "interface": interface,
                    "label": f"{self._interface_label(interface, host)} ({host})",
                }
            )
        return options

    def _ipv4_for_interface(self, interface: str) -> str:
        try:
            result = subprocess.run(
                ["ifconfig", interface],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ""
        if result.returncode != 0:
            return ""
        match = re.search(r"^\s*inet (\d+\.\d+\.\d+\.\d+)\b", result.stdout, flags=re.MULTILINE)
        if not match:
            return ""
        host = match.group(1).strip()
        if not host or host == "127.0.0.1":
            return ""
        return host

    def _interface_label(self, interface: str, host: str) -> str:
        if interface.startswith("utun") or host.startswith("100."):
            return f"Tailscale or VPN ({interface})"
        if interface.startswith("en"):
            return f"Network interface ({interface})"
        if interface.startswith("bridge"):
            return f"Bridge interface ({interface})"
        return f"Interface {interface}"

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
