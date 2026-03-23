import unittest

from echo_mcp_chats import echo_text_for_message, should_echo_update, update_key


class McpEchoTests(unittest.TestCase):
    def test_echo_text_prefers_text(self):
        self.assertEqual(echo_text_for_message({"text": "hello", "media": "MessageMediaPhoto"}), "hello")

    def test_echo_text_uses_media_placeholder(self):
        self.assertEqual(echo_text_for_message({"text": "", "media": "MessageMediaPhoto"}), "[MessageMediaPhoto]")

    def test_should_echo_only_new_incoming_unseen_messages(self):
        update = {
            "kind": "new_message",
            "peer_id": "42",
            "message_id": 7,
            "incoming": True,
            "message": {"text": "hello"},
        }
        self.assertTrue(should_echo_update(update, seen=set()))
        self.assertFalse(should_echo_update(update, seen={update_key(update)}))
        self.assertFalse(should_echo_update({**update, "incoming": False}, seen=set()))
        self.assertFalse(should_echo_update({**update, "kind": "message_edited"}, seen=set()))
