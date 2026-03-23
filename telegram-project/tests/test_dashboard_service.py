import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon import types

from telegram_proxy.config import ProxyConfig
from telegram_proxy.dashboard_service import ProxyDashboardServer
from telegram_proxy.secrets_store import UpstreamSecrets


def _now():
    return datetime(2026, 3, 22, tzinfo=timezone.utc)


def _channel(channel_id: int, *, title: str, username: str, access_hash: int) -> types.Channel:
    return types.Channel(
        id=channel_id,
        title=title,
        username=username,
        access_hash=access_hash,
        megagroup=True,
        photo=types.ChatPhotoEmpty(),
        date=_now(),
    )


def _message(message_id: int, *, peer: types.PeerChannel, sender: types.PeerUser, text: str, out: bool = False) -> types.Message:
    return types.Message(
        id=message_id,
        peer_id=peer,
        from_id=sender,
        message=text,
        out=out,
        date=_now(),
    )


class _FakeUpstream:
    def __init__(self) -> None:
        self.chat = _channel(42, title="Cloud Chat", username="cloudroom", access_hash=4200)
        self.dialog = SimpleNamespace(
            entity=self.chat,
            title="Cloud Chat",
            is_user=False,
            is_group=True,
            is_channel=False,
        )
        self.history = SimpleNamespace(
            messages=[_message(7, peer=types.PeerChannel(42), sender=types.PeerUser(1000), text="hello dashboard")],
        )

    async def get_dialogs(self, limit=500):
        return [self.dialog]

    async def get_history(self, peer, limit=50):
        return self.history

    async def get_identity(self):
        return {
            "id": 1000,
            "name": "Dmitry Proxy",
            "phone": "79936003330",
            "username": "dimapush",
        }


class _FakeMcp:
    def __init__(self) -> None:
        self.is_running = True
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self):
        self.start_calls += 1
        self.is_running = True

    async def stop(self):
        self.stop_calls += 1
        self.is_running = False


class _FakeTelegramAuth:
    def __init__(self) -> None:
        self.saved_payloads: list[tuple[str, str, str]] = []
        self.codes: list[str] = []
        self.passwords: list[str] = []
        self.requested_phone = ""
        self.cleared_session = False

    async def get_status(self):
        return {
            "keychain_backend": "macOS Keychain",
            "has_api_credentials": bool(self.saved_payloads),
            "has_session": bool(self.passwords) and not self.cleared_session,
            "phone": self.requested_phone or "+15550000000",
            "saved_phone": "+15550000000" if self.passwords and not self.cleared_session else None,
            "next_step": "ready" if self.passwords and not self.cleared_session else ("code" if self.codes else "credentials"),
            "pending_phone": self.requested_phone or None,
            "last_error": None,
        }

    async def save_credentials(self, *, api_id: str, api_hash: str, phone: str):
        self.saved_payloads.append((api_id, api_hash, phone))
        self.requested_phone = phone
        return await self.get_status()

    async def request_code(self, *, phone: str = ""):
        self.requested_phone = phone
        self.codes.append("requested")
        return await self.get_status()

    async def submit_code(self, *, code: str):
        self.codes.append(code)
        return {
            **(await self.get_status()),
            "next_step": "password",
        }

    async def submit_password(self, *, password: str):
        self.passwords.append(password)
        self.cleared_session = False
        return await self.get_status()

    async def clear_saved_session(self):
        self.cleared_session = True
        self.passwords.clear()
        return await self.get_status()

    async def clear_saved_auth(self):
        self.cleared_session = True
        self.saved_payloads.clear()
        self.codes.clear()
        self.passwords.clear()
        self.requested_phone = ""
        return await self.get_status()


class _FakeSecretStore:
    def __init__(self) -> None:
        self.is_available = True
        self.mcp_token = "test-mcp-token"
        self.upstream = UpstreamSecrets()

    def load_upstream_secrets(self) -> UpstreamSecrets:
        return self.upstream

    def rotate_mcp_token(self) -> str:
        self.mcp_token = "rotated-test-token"
        return self.mcp_token


class _FakeWhatsApp:
    def __init__(self) -> None:
        self.phone = ""
        self.has_session = False
        self.chats = [
            {
                "jid": "12345@s.whatsapp.net",
                "title": "Cloud WA Chat",
                "kind": "dm",
                "labels": ["Cloud"],
                "last_message_at": _now().isoformat(),
            }
        ]

    async def get_status(self, *, limit=500):
        return {
            "ok": True,
            "available": True,
            "connected": self.has_session,
            "has_session": self.has_session,
            "pairing_code": "123-456" if not self.has_session else None,
            "pairing_phone": self.phone or None,
            "cloud_label_name": "Cloud",
            "cloud_label_found": True,
            "chats": self.chats[:limit],
            "auth_dir": "/tmp/wa-auth",
            "connection": "open" if self.has_session else "connecting",
            "last_error": None,
            "me": {"name": "WA User"} if self.has_session else None,
        }

    async def get_chat(self, jid, *, limit=80):
        return {
            "ok": True,
            "chat": self.chats[0] if jid == self.chats[0]["jid"] else None,
            "messages": [
                {
                    "id": "wamid-1",
                    "chat_id": jid,
                    "text": "hello whatsapp",
                    "date": _now().isoformat(),
                    "from_me": False,
                    "kind": "conversation",
                }
            ][:limit],
        }

    async def request_pairing_code(self, *, phone: str):
        self.phone = phone
        return await self.get_status()

    async def logout(self):
        self.has_session = False
        return await self.get_status()


class _FakeIMessage:
    def __init__(self) -> None:
        self.all_chats = [
            {
                "chat_id": "any;-;+15550000000",
                "title": "Alice",
                "kind": "dm",
                "participants": ["+15550000000"],
                "participant_count": 1,
                "last_message_at": _now().isoformat(),
                "last_message_text": "hello imessage",
                "unread_count": 0,
            }
        ]
        self.visible_chat_ids = {self.all_chats[0]["chat_id"]}

    async def get_status(self, *, limit=500):
        visible_chats = [chat for chat in self.all_chats if chat["chat_id"] in self.visible_chat_ids]
        return {
            "ok": True,
            "available": True,
            "connected": True,
            "has_session": True,
            "messages_app_accessible": True,
            "database_accessible": False,
            "database_error": "Full Disk Access required",
            "accounts": [{"id": "account-1", "connection": "connected", "enabled": True, "service_type": "iMessage"}],
            "all_chats": self.all_chats[:limit],
            "visible_chats": visible_chats[:limit],
            "visible_chat_ids": [chat["chat_id"] for chat in visible_chats[:limit]],
            "chats": visible_chats[:limit],
            "db_path": "/Users/dmitry/Library/Messages/chat.db",
            "last_error": "Full Disk Access required",
        }

    async def get_local_chat(self, chat_id, *, limit=80):
        return {
            "ok": True,
            "chat": self.all_chats[0] if chat_id == self.all_chats[0]["chat_id"] else None,
            "messages": [
                {
                    "id": "imsg-1",
                    "chat_id": chat_id,
                    "text": "hello imessage",
                    "date": _now().isoformat(),
                    "from_me": False,
                    "kind": "text",
                }
            ][:limit],
        }

    async def set_chat_visibility(self, *, chat_id: str, visible: bool):
        if visible:
            self.visible_chat_ids.add(chat_id)
        else:
            self.visible_chat_ids.discard(chat_id)
        return await self.get_status()


class DashboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = ProxyConfig(
            dashboard_host="127.0.0.1",
            dashboard_port=0,
            cloud_folder_name="Cloud",
            imessage_enabled=True,
            mcp_token="test-mcp-token",
        )
        self.telegram_auth = _FakeTelegramAuth()
        self.secret_store = _FakeSecretStore()
        self.whatsapp = _FakeWhatsApp()
        self.imessage = _FakeIMessage()
        self.mcp = _FakeMcp()
        self.server = ProxyDashboardServer(
            self.config,
            _FakeUpstream(),
            self.mcp,
            self.telegram_auth,
            whatsapp=self.whatsapp,
            imessage=self.imessage,
            secret_store=self.secret_store,
        )
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.tmp.cleanup()

    async def test_rejects_old_web_dashboard_routes(self):
        status, body = await self._get("/")
        self.assertEqual(status, 404)
        self.assertEqual(body, "Not Found")

        status, body = await self._get("/styles.css")
        self.assertEqual(status, 404)
        self.assertEqual(body, "Not Found")

        status, body = await self._get("/app.js")
        self.assertEqual(status, 404)
        self.assertEqual(body, "Not Found")

    async def test_serves_overview_and_chat_json(self):
        status, body = await self._get("/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["config"]["cloud_folder_name"], "Cloud")
        self.assertTrue(payload["config"]["imessage_enabled"])
        self.assertEqual(payload["chats"][0]["title"], "Cloud Chat")
        self.assertEqual(payload["upstream"]["phone"], "79936003330")
        self.assertEqual(payload["telegram_auth"]["keychain_backend"], "macOS Keychain")
        self.assertEqual(payload["whatsapp"]["chats"][0]["title"], "Cloud WA Chat")
        self.assertEqual(payload["imessage"]["chats"][0]["title"], "Alice")
        self.assertEqual(payload["imessage"]["all_chats"][0]["title"], "Alice")
        self.assertTrue(payload["mcp"]["token_hidden"])
        self.assertTrue(payload["mcp"]["listening"])
        self.assertTrue(payload["mcp"]["bind_options"])
        self.assertEqual(payload["mcp"]["scheme"], "http")
        self.assertEqual(payload["mcp"]["endpoint"], "http://127.0.0.1:8791/mcp")
        self.assertNotIn("token", payload["mcp"])

        status, body = await self._get("/api/chat?peer_id=-1000000000042")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["chat"]["title"], "Cloud Chat")
        self.assertEqual(payload["messages"][0]["text"], "hello dashboard")

        status, body = await self._get("/api/whatsapp/chat?jid=12345%40s.whatsapp.net")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["chat"]["title"], "Cloud WA Chat")
        self.assertEqual(payload["messages"][0]["text"], "hello whatsapp")

        status, body = await self._get("/api/imessage/chat?chat_id=any%3B-%3B%2B15550000000")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["chat"]["title"], "Alice")
        self.assertEqual(payload["messages"][0]["text"], "hello imessage")

    async def test_mcp_token_routes(self):
        status, body = await self._get("/api/mcp/token")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["token"], "test-mcp-token")
        self.assertFalse(payload["env_managed"])

        status, body = await self._post("/api/mcp/token/rotate", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertNotEqual(payload["token"], "test-mcp-token")
        self.assertEqual(self.config.mcp_token, payload["token"])
        self.assertEqual(self.secret_store.mcp_token, payload["token"])

    async def test_mcp_config_route_updates_host_and_port(self):
        status, body = await self._post("/api/mcp/config", {"host": "100.92.237.54", "port": 8795})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scheme"], "http")
        self.assertEqual(payload["host"], "100.92.237.54")
        self.assertEqual(payload["port"], 8795)
        self.assertTrue(payload["listening"])
        self.assertEqual(self.config.mcp_host, "100.92.237.54")
        self.assertEqual(self.config.mcp_port, 8795)
        self.assertEqual(self.mcp.stop_calls, 1)
        self.assertEqual(self.mcp.start_calls, 1)

        status, body = await self._get("/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["mcp"]["scheme"], "http")
        self.assertEqual(payload["mcp"]["host"], "100.92.237.54")
        self.assertEqual(payload["mcp"]["port"], 8795)

    async def test_mcp_config_route_switches_to_https_when_tls_files_exist(self):
        cert_path = Path(self.tmp.name) / "mcp-cert.pem"
        key_path = Path(self.tmp.name) / "mcp-key.pem"
        cert_path.write_text("dummy cert\n", encoding="utf-8")
        key_path.write_text("dummy key\n", encoding="utf-8")
        self.config.mcp_tls_cert_name = str(cert_path)
        self.config.mcp_tls_key_name = str(key_path)

        status, body = await self._post("/api/mcp/config", {"host": "100.92.237.54", "port": 8795, "scheme": "https"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["scheme"], "https")
        self.assertEqual(payload["endpoint"], "https://100.92.237.54:8795/mcp")
        self.assertEqual(self.config.mcp_scheme, "https")
        self.assertEqual(self.config.mcp_host, "100.92.237.54")
        self.assertEqual(self.config.mcp_port, 8795)

    async def test_mcp_config_route_rejects_https_without_tls_files(self):
        status, body = await self._post("/api/mcp/config", {"host": "100.92.237.54", "port": 8795, "scheme": "https"})
        self.assertEqual(status, 400)
        payload = json.loads(body)
        self.assertIn("TP_MCP_TLS_CERT", payload["error"])
        self.assertEqual(self.config.mcp_scheme, "http")

    async def test_telegram_auth_routes(self):
        status, body = await self._post(
            "/api/telegram/auth/save",
            {"api_id": "12345", "api_hash": "secret", "phone": "+15550000000"},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["has_api_credentials"])
        self.assertEqual(self.telegram_auth.saved_payloads[-1][0], "12345")

        status, body = await self._post("/api/telegram/auth/request-code", {"phone": "+15550000000"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["next_step"], "code")

        status, body = await self._post("/api/telegram/auth/submit-code", {"code": "11111"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["next_step"], "password")

        status, body = await self._post("/api/telegram/auth/submit-password", {"password": "hunter2"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["has_session"])
        self.assertEqual(payload["next_step"], "ready")

        status, body = await self._post("/api/telegram/auth/clear-session", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["has_api_credentials"])
        self.assertFalse(payload["has_session"])
        self.assertIsNone(payload["saved_phone"])

        status, body = await self._post("/api/telegram/auth/clear", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["has_api_credentials"])
        self.assertFalse(payload["has_session"])

    async def test_whatsapp_auth_routes(self):
        status, body = await self._get("/api/whatsapp/auth")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["pairing_code"], "123-456")
        self.assertEqual(payload["cloud_label_name"], "Cloud")

        status, body = await self._post("/api/whatsapp/auth/request-pairing-code", {"phone": "15550000000"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["pairing_phone"], "15550000000")

        status, body = await self._post("/api/whatsapp/auth/logout", {})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["has_session"])

    async def test_imessage_auth_route(self):
        status, body = await self._get("/api/imessage/auth")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["chats"][0]["title"], "Alice")
        self.assertEqual(payload["all_chats"][0]["title"], "Alice")

    async def test_imessage_auth_route_returns_disabled_state(self):
        self.config.imessage_enabled = False
        self.config.imessage_messages_app_accessible = True
        self.config.imessage_database_accessible = True

        status, body = await self._get("/api/imessage/auth")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["available"])
        self.assertTrue(payload["messages_app_accessible"])
        self.assertTrue(payload["database_accessible"])
        self.assertEqual(payload["all_chats"], [])
        self.assertIn("Full Disk Access", payload["automation_hint"])

    async def test_imessage_visible_chat_toggle_route(self):
        status, body = await self._post(
            "/api/imessage/visible-chats",
            {"chat_id": "any;-;+15550000000", "visible": False},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["visible_chats"], [])
        self.assertEqual(payload["visible_chat_ids"], [])

    async def test_imessage_enable_toggle_route(self):
        self.config.imessage_enabled = False

        status, body = await self._post("/api/imessage/enabled", {"enabled": True})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["message"], "Messages integration enabled.")
        self.assertTrue(self.config.imessage_enabled)

        status, body = await self._post("/api/imessage/enabled", {"enabled": False})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["message"], "Messages integration disabled.")
        self.assertFalse(self.config.imessage_enabled)

    async def _get(self, path: str) -> tuple[int, str]:
        reader, writer = await asyncio.open_connection(self.config.dashboard_host, self.config.dashboard_port)
        writer.write(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {self.config.dashboard_host}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, body = raw.split(b"\r\n\r\n", 1)
        status = int(head.splitlines()[0].split()[1])
        return status, body.decode("utf-8")

    async def _post(self, path: str, payload: dict[str, object]) -> tuple[int, str]:
        body = json.dumps(payload).encode("utf-8")
        reader, writer = await asyncio.open_connection(self.config.dashboard_host, self.config.dashboard_port)
        writer.write(
            (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {self.config.dashboard_host}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + body
        )
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, response_body = raw.split(b"\r\n\r\n", 1)
        status = int(head.splitlines()[0].split()[1])
        return status, response_body.decode("utf-8")
