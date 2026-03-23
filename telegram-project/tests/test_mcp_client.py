import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon import types

from list_mcp_chats import McpClient, render_chats
from telegram_proxy.config import ProxyConfig
from telegram_proxy.mcp_service import McpServer
from telegram_proxy.update_bus import UpdateBus


def _now():
    return datetime(2026, 3, 23, tzinfo=timezone.utc)


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


class _FakeUpstream:
    def __init__(self) -> None:
        self.chat = _channel(42, title="Cloud Chat", username="cloudroom", access_hash=4200)
        self.update_bus = UpdateBus()
        self.dialog = SimpleNamespace(
            entity=self.chat,
            title="Cloud Chat",
            is_user=False,
            is_group=True,
            is_channel=False,
        )

    async def get_dialogs(self, limit=500):
        return [self.dialog]

    async def get_history(self, peer, limit=50):  # pragma: no cover - not used here
        raise AssertionError("get_history should not be called")

    async def get_identity(self):
        return {"id": 1, "name": "Proxy User"}


class McpClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = ProxyConfig(
            mcp_host="127.0.0.1",
            mcp_port=0,
            mcp_token="test-token",
        )
        self.server = McpServer(self.config, _FakeUpstream())
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.tmp.cleanup()

    async def test_lists_chats_via_tool_call(self):
        client = McpClient(
            host=self.config.mcp_host,
            port=self.config.mcp_port,
            path=self.config.mcp_path,
            token=self.config.mcp_token,
        )
        try:
            await asyncio.to_thread(client.initialize)
            await asyncio.to_thread(client.notify_initialized)
            chats = await asyncio.to_thread(client.list_chats, limit=10)
        finally:
            await asyncio.to_thread(client.close)

        self.assertEqual(chats[0]["peer_id"], "-1000000000042")
        self.assertEqual(chats[0]["title"], "Cloud Chat")
        self.assertEqual(chats[0]["kind"], "group")

    def test_render_chats_outputs_tab_separated_rows(self):
        rendered = render_chats(
            [
                {
                    "peer_id": "-1000000000042",
                    "kind": "group",
                    "title": "Cloud Chat",
                    "username": "cloudroom",
                }
            ]
        )
        self.assertEqual(rendered, "-1000000000042\tgroup\tCloud Chat\tcloudroom")
