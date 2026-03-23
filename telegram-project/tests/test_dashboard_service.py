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
        )
        self.registry = DownstreamRegistry(self.config.downstream_registry_path)
        self.registry.issue_session(label="dashboard-test", host="127.0.0.1", port=9001)
        self.server = ProxyDashboardServer(self.config, _FakeUpstream(), self.registry, _FakeMTProto())
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.tmp.cleanup()

    async def test_serves_dashboard_html(self):
        status, body = await self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Telethon service control room", body)
        self.assertIn("Configuration", body)
        self.assertIn("Chats", body)
        self.assertIn("APIs", body)
        self.assertIn("/styles.css", body)
        self.assertIn("/app.js", body)

    async def test_serves_dashboard_static_assets(self):
        status, body = await self._get("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(".workspace", body)

        status, body = await self._get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("loadOverview()", body)

    async def test_serves_overview_and_chat_json(self):
        status, body = await self._get("/api/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["config"]["cloud_folder_name"], "Cloud")
        self.assertEqual(payload["clients"][0]["label"], "openclaw")
        self.assertEqual(payload["chats"][0]["title"], "Cloud Chat")
        self.assertEqual(payload["upstream"]["phone"], "79936003330")
        self.assertEqual(payload["downstream_credentials"][0]["label"], "dashboard-test")
        self.assertTrue(payload["downstream_credentials"][0]["session_string"])

        status, body = await self._get("/api/chat?peer_id=-1000000000042")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["chat"]["title"], "Cloud Chat")
        self.assertEqual(payload["messages"][0]["text"], "hello dashboard")

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
