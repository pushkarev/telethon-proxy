import asyncio
import json
import unittest
from types import SimpleNamespace

from telegram_proxy.compat import DownstreamSession
from telegram_proxy.server import ProxyServer
from telegram_proxy.session_state import VirtualUpdateState
from telegram_proxy.update_bus import UpdateEnvelope


class FakeWriter:
    def __init__(self):
        self.buffer = []

    def write(self, data: bytes):
        self.buffer.append(data)

    async def drain(self):
        return None


class ServerAuthUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_push_updates_requires_authenticated_session(self):
        server = ProxyServer.__new__(ProxyServer)
        server.dispatcher = SimpleNamespace(
            _serialize_message=lambda msg: {"id": msg.id},
            _serialize_state=lambda state: {"pts": state.pts},
        )
        queue = asyncio.Queue()
        writer = FakeWriter()
        session = DownstreamSession(session_id='s', state=VirtualUpdateState(), principal=None)
        task = asyncio.create_task(server._push_updates(writer, queue, session))
        await queue.put(UpdateEnvelope(kind='new_message', payload=SimpleNamespace(id=1), peer_id=42, message_id=1))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(writer.buffer, [])

    async def test_push_updates_emits_authenticated_updates(self):
        server = ProxyServer.__new__(ProxyServer)
        server.dispatcher = SimpleNamespace(
            _serialize_message=lambda msg: {"id": msg.id},
            _serialize_state=lambda state: {"pts": state.pts},
        )
        queue = asyncio.Queue()
        writer = FakeWriter()
        session = DownstreamSession(session_id='s', state=VirtualUpdateState(), principal=SimpleNamespace(phone='+1'))
        task = asyncio.create_task(server._push_updates(writer, queue, session))
        await queue.put(UpdateEnvelope(kind='new_message', payload=SimpleNamespace(id=1), peer_id=42, message_id=1, incoming=True, mentioned=True))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        body = json.loads(writer.buffer[0].decode())
        self.assertEqual(body['update']['kind'], 'new_message')
        self.assertTrue(body['update']['incoming'])
        self.assertTrue(body['update']['mentioned'])
