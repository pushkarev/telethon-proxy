import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_proxy.compat import CompatDispatcher, DownstreamSession
from telegram_proxy.session_state import VirtualUpdateState


class FakeUpstream:
    def __init__(self):
        self.refreshed = False

    async def refresh_policy(self):
        self.refreshed = True
        return SimpleNamespace(allowed_peers={1, 2, 3})

    async def get_dialogs(self, limit=100):
        from telethon import types
        entity = types.PeerChat(42)
        return [SimpleNamespace(entity=entity, name='Cloud Chat', unread_count=0, is_user=False, is_group=True, is_channel=False)]

    async def get_history(self, peer, limit=100):
        from telethon import types
        msg = SimpleNamespace(id=7, peer_id=types.PeerChat(42), from_id=types.PeerUser(99), message='hello', date=datetime(2026, 3, 17, tzinfo=timezone.utc))
        user = SimpleNamespace(id=99, username='alice', first_name='Alice', last_name=None, bot=False)
        chat = SimpleNamespace(id=42, title='Cloud Chat', username=None)
        return SimpleNamespace(messages=[msg], users=[user], chats=[chat], dropped_count=0)

    async def send_message(self, peer, message):
        from telethon import types
        return SimpleNamespace(id=8, peer_id=types.PeerChat(42), from_id=types.PeerUser(99), message=message, date=datetime(2026, 3, 17, tzinfo=timezone.utc))

    async def mark_read(self, peer):
        return None

    async def list_participants(self, peer, limit=100):
        return [SimpleNamespace(id=99, username='alice', first_name='Alice', last_name=None, bot=False)]


class CompatDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dispatcher = CompatDispatcher(FakeUpstream())
        self.session = DownstreamSession(session_id='s1', state=VirtualUpdateState())

    async def test_get_state(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_state'})
        self.assertTrue(result['ok'])
        self.assertIn('pts', result['state'])

    async def test_get_dialogs(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_dialogs'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['dialogs'][0]['name'], 'Cloud Chat')

    async def test_get_history(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'get_history', 'peer': 42})
        self.assertTrue(result['ok'])
        self.assertEqual(result['messages'][0]['text'], 'hello')

    async def test_send_message_advances_state(self):
        before = self.session.state.pts
        result = await self.dispatcher.dispatch(self.session, {'method': 'send_message', 'peer': 42, 'message': 'hi'})
        self.assertTrue(result['ok'])
        self.assertGreater(result['state']['pts'], before)

    async def test_list_participants(self):
        result = await self.dispatcher.dispatch(self.session, {'method': 'list_participants', 'peer': 42})
        self.assertTrue(result['ok'])
        self.assertEqual(result['participants'][0]['username'], 'alice')


if __name__ == '__main__':
    unittest.main()
