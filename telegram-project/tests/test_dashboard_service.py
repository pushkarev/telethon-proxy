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
from telegram_proxy.downstream_registry import DownstreamRegistry
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


class _FakeMTProto:
    def __init__(self) -> None:
        self.is_running = True

    def active_connections_snapshot(self):
        return [
            {
                "connection_id": 1,
                "key_id": 123,
                "label": "openclaw",
                "phone": "+15550000000",
                "connected_at": _now().isoformat(),
                "remote_addr": "100.64.0.2:50000",
                "authorized": True,
            }
        ]

    async def start(self):
        self.is_running = True

    async def stop(self):
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


class DashboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        registry_path = Path(self.tmp.name) / "registry.json"
        self.config = ProxyConfig(
            dashboard_host="127.0.0.1",
            dashboard_port=0,
            downstream_host="100.92.237.54",
            mtproto_port=9001,
            downstream_api_id=900000,
            cloud_folder_name="Cloud",
            downstream_registry_name=str(registry_path),
            mcp_token="test-mcp-token",
        )
        self.registry = DownstreamRegistry(self.config.downstream_registry_path)
        self.registry.issue_session(label="dashboard-test", host="127.0.0.1", port=9001)
        self.telegram_auth = _FakeTelegramAuth()
        self.secret_store = _FakeSecretStore()
        self.whatsapp = _FakeWhatsApp()
        self.server = ProxyDashboardServer(
            self.config,
            _FakeUpstream(),
            self.registry,
            _FakeMTProto(),
            self.telegram_auth,
            whatsapp=self.whatsapp,
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
        self.assertTrue(payload["config"]["mtproto_enabled"])
        self.assertTrue(payload["config"]["mtproto_listening"])
        self.assertEqual(payload["clients"][0]["label"], "openclaw")
        self.assertEqual(payload["chats"][0]["title"], "Cloud Chat")
        self.assertEqual(payload["upstream"]["phone"], "79936003330")
        self.assertEqual(payload["downstream_credentials"][0]["label"], "dashboard-test")
        self.assertTrue(payload["downstream_credentials"][0]["session_string"])
        self.assertEqual(payload["telegram_auth"]["keychain_backend"], "macOS Keychain")
        self.assertEqual(payload["whatsapp"]["chats"][0]["title"], "Cloud WA Chat")
        self.assertTrue(payload["mcp"]["token_hidden"])
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

    async def test_mtproto_enable_toggle_route(self):
        status, body = await self._post("/api/mtproto/enabled", {"enabled": False})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["listening"])
        self.assertFalse(self.config.mtproto_enabled)

        status, body = await self._get("/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["config"]["mtproto_enabled"])
        self.assertFalse(payload["config"]["mtproto_listening"])

        status, body = await self._post("/api/mtproto/enabled", {"enabled": True})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["enabled"])
        self.assertTrue(payload["listening"])
        self.assertTrue(self.config.mtproto_enabled)

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
