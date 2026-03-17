import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon import types

from telegram_proxy.compat import CompatDispatcher, DownstreamSession
from telegram_proxy.config import ProxyConfig
from telegram_proxy.downstream_auth import DownstreamAuthService
from telegram_proxy.session_state import VirtualUpdateState


class FakeUpstream:
    def __init__(self):
        self.refreshed = False

    async def refresh_policy(self):
        self.refreshed = True
        return SimpleNamespace(allowed_peers={1, 2, 3})

    async def resolve_peer(self, peer):
        if str(peer) == '42':
            return types.PeerChat(42)
        return types.PeerUser(99)

    async def get_dialogs(self, limit=100):
        entity = types.PeerChat(42)
        return [SimpleNamespace(entity=entity, name='Cloud Chat', unread_count=0, is_user=False, is_group=True, is_channel=False)]

    async def get_history(self, peer, limit=100):
        msg = SimpleNamespace(id=7, peer_id=types.PeerChat(42), from_id=types.PeerUser(99), message='hello', mentioned=False, out=False, date=datetime(2026, 3, 17, tzinfo=timezone.utc))
        user = SimpleNamespace(id=99, username='alice', first_name='Alice', last_name=None, bot=False)
        chat = SimpleNamespace(id=42, title='Cloud Chat', username=None)
        return SimpleNamespace(messages=[msg], users=[user], chats=[chat], dropped_count=0)

    async def get_mentions(self, peer, limit=100):
        msg = SimpleNamespace(id=9, peer_id=types.PeerChat(42), from_id=types.PeerUser(99), message='@you hello', mentioned=True, out=False, date=datetime(2026, 3, 17, tzinfo=timezone.utc))
        return SimpleNamespace(messages=[msg], users=[], chats=[], dropped_count=0)

    async def send_message(self, peer, message):
        return SimpleNamespace(id=8, peer_id=types.PeerChat(42), from_id=types.PeerUser(99), message=message, mentioned=False, out=True, date=datetime(2026, 3, 17, tzinfo=timezone.utc))

    async def mark_read(self, peer):
        return None

    async def list_participants(self, peer, limit=100):
        return [SimpleNamespace(id=99, username='alice', first_name='Alice', last_name=None, bot=False)]


class CompatDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = ProxyConfig(
            downstream_api_id=123,
            downstream_api_hash='hash123',
            downstream_login_code='55555',
            downstream_password='secret',
        )
        auth = DownstreamAuthService(config)
        self.dispatcher = CompatDispatcher(FakeUpstream(), auth)
        self.session = DownstreamSession(session_id='s1', state=VirtualUpdateState())
        sent = await self.dispatcher.dispatch(self.session, {
            'method': 'auth_send_code',
            'phone': '+10000000000',
            'api_id': 123,
            'api_hash': 'hash123',
        })
        await self.dispatcher.dispatch(self.session, {
            'method': 'auth_sign_in',
            'phone': '+10000000000',
            'phone_code_hash': sent['phone_code_hash'],
            'code': '55555',
            'password': 'secret',
        })

    async def test_get_state(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_state'})
        self.assertTrue(result['ok'])
        self.assertIn('pts', result['state'])

    async def test_resolve_peer(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'resolve_peer', 'peer': '42'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['peer']['class'], 'PeerChat')

    async def test_get_dialogs(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_dialogs'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['dialogs'][0]['name'], 'Cloud Chat')

    async def test_get_history(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_history', 'peer': 42})
        self.assertTrue(result['ok'])
        self.assertEqual(result['messages'][0]['text'], 'hello')

    async def test_get_mentions(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_mentions', 'peer': 42})
        self.assertTrue(result['ok'])
        self.assertTrue(result['messages'][0]['mentioned'])

    async def test_send_message_advances_state(self):
        before = self.session.state.pts
        result = await self.dispatcher.dispatch(self.session, {'method': 'send_message', 'peer': 42, 'message': 'hi'})
        self.assertTrue(result['ok'])
        self.assertGreater(result['state']['pts'], before)

    async def test_list_participants(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'list_participants', 'peer': 42})
        self.assertTrue(result['ok'])
        self.assertEqual(result['participants'][0]['username'], 'alice')

    async def test_rejects_unauthenticated_requests(self):
        config = ProxyConfig(downstream_api_id=123, downstream_api_hash='hash123')
        dispatcher = CompatDispatcher(FakeUpstream(), DownstreamAuthService(config))
        session = DownstreamSession(session_id='s2', state=VirtualUpdateState())
        with self.assertRaises(PermissionError):
            await dispatcher.dispatch(session, {'method': 'get_dialogs'})


if __name__ == '__main__':
    unittest.main()
