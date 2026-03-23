import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon import types, utils

from telegram_proxy.config import ProxyConfig
from telegram_proxy.mcp_service import McpServer
from telegram_proxy.update_bus import UpdateBus, UpdateEnvelope


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
        self.sender = types.User(id=1000, first_name="Alice", access_hash=5000)
        self.update_bus = UpdateBus()
        self.dialog = SimpleNamespace(
            entity=self.chat,
            title="Cloud Chat",
            is_user=False,
            is_group=True,
            is_channel=False,
        )
        self.messages = [
            _message(7, peer=types.PeerChannel(42), sender=types.PeerUser(1000), text="hello mcp"),
        ]
        self.last_read = None

    async def get_dialogs(self, limit=500):
        return [self.dialog]

    async def get_history(self, peer, limit=50):
        return SimpleNamespace(messages=self.messages[:limit], chats=[self.chat], users=[self.sender], dropped_count=0)

    async def search_messages(self, peer, *, query, filter, limit=20, **_kwargs):
        matched = [message for message in self.messages if query.lower() in (message.message or "").lower()]
        return SimpleNamespace(messages=matched[:limit], chats=[self.chat], users=[self.sender], dropped_count=0)

    async def search_all_messages(self, *, query, filter, limit=20, **_kwargs):
        matched = [message for message in self.messages if query.lower() in (message.message or "").lower()]
        return SimpleNamespace(messages=matched[:limit], chats=[self.chat], users=[self.sender], dropped_count=0)

    async def send_message(self, peer, text, *, reply_to=None):
        message = _message(8, peer=types.PeerChannel(42), sender=types.PeerUser(1), text=text, out=True)
        message.reply_to = reply_to
        self.messages.insert(0, message)
        return message

    async def delete_messages(self, peer, message_ids, *, revoke=True):
        self.messages = [message for message in self.messages if message.id not in set(message_ids)]
        return SimpleNamespace(pts=1, pts_count=len(message_ids))

    async def read_history(self, peer, max_id):
        self.last_read = max_id
        return None

    async def mark_read(self, peer):
        self.last_read = 0
        return None

    async def list_participants(self, peer, limit=100):
        return [self.sender]

    async def get_identity(self):
        return {"id": 1, "name": "Proxy User", "phone": "79936003330", "username": "dimapush"}


class _FakeWhatsApp:
    def __init__(self) -> None:
        self.chat = {
            "jid": "12345@s.whatsapp.net",
            "title": "Cloud WA Chat",
            "kind": "dm",
            "labels": ["Cloud"],
            "last_message_at": _now().isoformat(),
        }
        self.messages = [
            {
                "id": "wamid-1",
                "chat_id": self.chat["jid"],
                "text": "hello wa",
                "date": _now().isoformat(),
                "from_me": False,
                "kind": "conversation",
            }
        ]

    async def get_status(self, *, limit=500):
        return {
            "ok": True,
            "available": True,
            "connected": False,
            "has_session": False,
            "cloud_label_name": "Cloud",
            "cloud_label_found": True,
            "qr_available": True,
            "qr_raw": "qr-raw",
            "qr_ascii": "qr-ascii",
            "chats": [self.chat][:limit],
        }

    async def get_chats(self, *, limit=200):
        return {"ok": True, "chats": [self.chat][:limit]}

    async def get_chat(self, jid, *, limit=50):
        return {"ok": True, "chat": self.chat, "messages": self.messages[:limit]}

    async def send_message(self, *, jid: str, text: str):
        message = {
            "id": "wamid-2",
            "chat_id": jid,
            "text": text,
            "date": _now().isoformat(),
            "from_me": True,
            "kind": "conversation",
        }
        self.messages.append(message)
        return {"ok": True, "message": message}

    async def mark_read(self, *, jid: str, message_id: str | None = None):
        return {"ok": True, "marked": True, "message_id": message_id or self.messages[-1]["id"]}

    async def get_updates(self, *, limit=50):
        return {
            "ok": True,
            "updates": [
                {
                    "kind": "new_message",
                    "chat_id": self.chat["jid"],
                    "message_id": self.messages[-1]["id"],
                    "message": self.messages[-1],
                }
            ][:limit],
        }


class McpServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.config = ProxyConfig(
            mcp_host="127.0.0.1",
            mcp_port=0,
            mcp_token="test-token",
            downstream_host="100.92.237.54",
            mtproto_port=9001,
            downstream_api_id=900000,
        )
        self.server = McpServer(self.config, _FakeUpstream(), whatsapp=_FakeWhatsApp())
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.tmp.cleanup()

    async def test_requires_bearer_auth(self):
        status, payload, _ = await self._request("POST", self.config.mcp_path, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "Unauthorized")

    async def test_initialize_and_tool_calls(self):
        status, payload, headers = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "tester"}},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["serverInfo"]["name"], "telethon-proxy-mcp")
        self.assertTrue(headers.get("mcp-session-id"))
        session_id = headers["mcp-session-id"]

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        tool_names = [tool["name"] for tool in payload["result"]["tools"]]
        self.assertIn("telegram.list_chats", tool_names)
        self.assertIn("telegram.get_messages", tool_names)
        self.assertIn("telegram.delete_messages", tool_names)
        self.assertIn("telegram.mark_read", tool_names)
        self.assertIn("whatsapp.list_chats", tool_names)
        self.assertIn("whatsapp.get_auth_status", tool_names)
        self.assertIn("whatsapp.get_messages", tool_names)

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "telegram.get_messages",
                    "arguments": {"peer": "-1000000000042", "limit": 10},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["structuredContent"]["ok"])
        self.assertEqual(payload["result"]["structuredContent"]["messages"][0]["text"], "hello mcp")

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 31,
                "method": "tools/call",
                "params": {
                    "name": "whatsapp.get_messages",
                    "arguments": {"jid": "12345@s.whatsapp.net", "limit": 10},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["structuredContent"]["ok"])
        self.assertEqual(payload["result"]["structuredContent"]["messages"][0]["text"], "hello wa")

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {
                    "name": "whatsapp.get_auth_status",
                    "arguments": {},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["structuredContent"]["qr_available"])

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 32,
                "method": "tools/call",
                "params": {
                    "name": "whatsapp.send_message",
                    "arguments": {"jid": "12345@s.whatsapp.net", "text": "wa reply"},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["structuredContent"]["message"]["text"], "wa reply")

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "telegram.send_message",
                    "arguments": {"peer": "-1000000000042", "text": "reply text", "reply_to_message_id": 7},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["structuredContent"]["message"]["text"], "reply text")

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "telegram.delete_messages",
                    "arguments": {"peer": "-1000000000042", "message_ids": [7]},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["structuredContent"]["ok"])

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "telegram.mark_read",
                    "arguments": {"peer": "-1000000000042", "max_id": 8},
                },
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["structuredContent"]["ok"])

    async def test_resources_read(self):
        status, payload, headers = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "reader"}},
            },
        )
        self.assertEqual(status, 200)
        session_id = headers["mcp-session-id"]

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        uris = [item["uri"] for item in payload["result"]["resources"]]
        self.assertIn("telegram://config", uris)
        self.assertIn("telegram://chats", uris)
        self.assertIn("telegram://updates", uris)
        self.assertIn("whatsapp://config", uris)
        self.assertIn("whatsapp://chats", uris)

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "resources/read",
                "params": {"uri": "telegram://config"},
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)
        self.assertIn("Proxy User", payload["result"]["contents"][0]["text"])

    async def test_subscription_stream_receives_update_notification(self):
        status, payload, headers = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "subscriber"}},
            },
        )
        self.assertEqual(status, 200)
        session_id = headers["mcp-session-id"]
        stream_reader, stream_writer = await asyncio.open_connection(self.config.mcp_host, self.config.mcp_port)
        stream_writer.write(
            (
                f"GET {self.config.mcp_path} HTTP/1.1\r\n"
                f"Host: {self.config.mcp_host}\r\n"
                f"Authorization: Bearer {self.config.mcp_token}\r\n"
                f"Mcp-Session-Id: {session_id}\r\n"
                "Accept: text/event-stream\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        await stream_writer.drain()
        await stream_reader.readuntil(b"\r\n\r\n")
        await stream_reader.readuntil(b"\n\n")

        status, payload, _ = await self._request(
            "POST",
            self.config.mcp_path,
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "resources/subscribe",
                "params": {"uri": "telegram://updates"},
            },
            session_id=session_id,
        )
        self.assertEqual(status, 200)

        pushed = _message(99, peer=types.PeerChannel(42), sender=types.PeerUser(1000), text="live update", out=False)
        await self.server.upstream.update_bus.publish(
            UpdateEnvelope(kind="new_message", payload=pushed, peer_id=utils.get_peer_id(pushed.peer_id), message_id=pushed.id)
        )

        for _ in range(3):
            chunk = await asyncio.wait_for(stream_reader.readuntil(b"\n\n"), timeout=2)
            if "notifications/resources/updated" in chunk.decode("utf-8"):
                break
        else:
            self.fail("did not receive update notification")

        stream_writer.close()
        await stream_writer.wait_closed()

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: str | None = "test-token",
        session_id: str | None = None,
    ):
        reader, writer = await asyncio.open_connection(self.config.mcp_host, self.config.mcp_port)
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = [
            f"{method} {path} HTTP/1.1",
            f"Host: {self.config.mcp_host}",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        if token is not None:
            headers.append(f"Authorization: Bearer {token}")
        if session_id is not None:
            headers.append(f"Mcp-Session-Id: {session_id}")
        request = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body
        writer.write(request)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, body = raw.split(b"\r\n\r\n", 1)
        lines = head.decode("utf-8").split("\r\n")
        status = int(lines[0].split()[1])
        parsed_headers = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            parsed_headers[name.strip().lower()] = value.strip()
        return status, json.loads(body.decode("utf-8")), parsed_headers
