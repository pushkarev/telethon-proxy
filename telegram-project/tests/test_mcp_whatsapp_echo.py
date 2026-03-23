import unittest

from echo_mcp_whatsapp import echo_text_for_message, should_echo_update, update_key


class McpWhatsAppEchoTests(unittest.TestCase):
    def test_echo_text_prefers_text(self):
        self.assertEqual(echo_text_for_message({"text": "hello", "kind": "conversation"}), "hello")

    def test_echo_text_uses_kind_placeholder(self):
        self.assertEqual(echo_text_for_message({"text": "", "kind": "imageMessage"}), "[imageMessage]")

    def test_should_echo_only_new_incoming_unseen_messages(self):
        update = {
            "kind": "new_message",
            "chat_id": "12345@s.whatsapp.net",
            "message_id": "wamid-1",
            "message": {"text": "hello", "from_me": False},
        }
        self.assertTrue(should_echo_update(update, seen=set()))
        self.assertFalse(should_echo_update(update, seen={update_key(update)}))
        self.assertFalse(should_echo_update({**update, "kind": "message_edited"}, seen=set()))
        self.assertFalse(should_echo_update({**update, "message": {"text": "hello", "from_me": True}}, seen=set()))


if __name__ == "__main__":
    unittest.main()
