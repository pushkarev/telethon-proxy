import unittest

from telegram_proxy.imessage_bridge import IMessageBridge, decode_attributed_body


class IMessageBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = IMessageBridge()

    def test_merge_chats_preserves_script_order_without_history_dates(self):
        merged = self.bridge._merge_chats(
            {
                "chat-b": {
                    "chat_id": "chat-b",
                    "title": "Beta",
                    "participants": ["+15550000002"],
                    "participant_count": 1,
                    "account_id": "account-1",
                    "script_order": 2,
                },
                "chat-a": {
                    "chat_id": "chat-a",
                    "title": "Alpha",
                    "participants": ["+15550000001"],
                    "participant_count": 1,
                    "account_id": "account-1",
                    "script_order": 1,
                },
            },
            {},
        )
        self.assertEqual([chat["chat_id"] for chat in merged], ["chat-a", "chat-b"])

    def test_merge_chats_prefers_recent_history_dates(self):
        merged = self.bridge._merge_chats(
            {
                "chat-a": {
                    "chat_id": "chat-a",
                    "title": "Alpha",
                    "participants": ["+15550000001"],
                    "participant_count": 1,
                    "account_id": "account-1",
                    "script_order": 2,
                },
                "chat-b": {
                    "chat_id": "chat-b",
                    "title": "Beta",
                    "participants": ["+15550000002"],
                    "participant_count": 1,
                    "account_id": "account-1",
                    "script_order": 1,
                },
            },
            {
                "chat-a": {
                    "chat_id": "chat-a",
                    "title": "Alpha",
                    "participants": ["+15550000001"],
                    "participant_count": 1,
                    "kind": "dm",
                    "last_message_at": "2026-03-23T10:00:00+00:00",
                    "last_message_text": "newer",
                    "unread_count": 0,
                },
                "chat-b": {
                    "chat_id": "chat-b",
                    "title": "Beta",
                    "participants": ["+15550000002"],
                    "participant_count": 1,
                    "kind": "dm",
                    "last_message_at": "2026-03-22T10:00:00+00:00",
                    "last_message_text": "older",
                    "unread_count": 0,
                },
            },
        )
        self.assertEqual([chat["chat_id"] for chat in merged], ["chat-a", "chat-b"])

    def test_decode_attributed_body_extracts_human_text(self):
        text, url = decode_attributed_body(
            "040B73747265616D747970656481E803840140848484124E5341747472696275746564537472696E67008484084E534F626A656374008592848484084E53537472696E67019484012B44536F7272792C20492063616EE2809974206865617220796F75206F7665722074686520736F756E64206F66206D79206F776E20617765736F6D656E6573732120F09F988E86840269490140928484840C4E5344696374696F6E617279009484016901928496961D5F5F6B494D4D657373616765506172744174747269627574654E616D658692848484084E534E756D626572008484074E5356616C7565009484012A84999900868686"
        )
        self.assertEqual(text, "Sorry, I can’t hear you over the sound of my own awesomeness! 😎")
        self.assertIsNone(url)

    def test_normalize_message_text_decodes_hex_summary_blob(self):
        text = self.bridge._normalize_message_text(
            "040B73747265616D747970656481E803840140848484194E534D757461626C6541747472696275746564537472696E67008484124E5341747472696275746564537472696E67008484084E534F626A6563740085928484840F4E534D757461626C65537472696E67018484084E53537472696E67019584012B3CD0ADD182D0BED18220D0B0D0B1D0BED0BDD0B5D0BDD18220D181D0BDD0BED0B2D0B020D0B220D181D0B5D182D0B82E20D0B1D0B8D0BBD0B0D0B9D0BD86840269490121928484840C4E5344696374696F6E617279009584016901928498981D5F5F6B494D4D657373616765506172744174747269627574654E616D658692848484084E534E756D626572008484074E5356616C7565009584012A849B9B00868686",
            None,
            [],
        )
        self.assertEqual(text, "Этот абонент снова в сети. билайн")
