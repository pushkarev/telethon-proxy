import unittest
import tempfile
from pathlib import Path
from unittest import mock

from telegram_proxy.imessage_bridge import IMessageBridge, IMessageBridgeError, decode_attributed_body


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

    def test_status_distinguishes_all_and_visible_chats(self):
        self.bridge._visible_chat_ids = {"chat-a"}
        chats = [
            {"chat_id": "chat-a", "title": "Alpha"},
            {"chat_id": "chat-b", "title": "Beta"},
        ]
        with mock.patch.object(self.bridge, "_safe_accounts", return_value=([], None)):
            with mock.patch.object(self.bridge, "_safe_chat_collection", return_value=(chats, None, True, None)):
                status = self.bridge._get_status_sync(50)

        self.assertEqual([chat["chat_id"] for chat in status["all_chats"]], ["chat-a", "chat-b"])
        self.assertEqual([chat["chat_id"] for chat in status["visible_chats"]], ["chat-a"])
        self.assertEqual([chat["chat_id"] for chat in status["chats"]], ["chat-a"])
        self.assertEqual(status["visible_chat_ids"], ["chat-a"])

    def test_get_chats_filters_to_visible_chats(self):
        self.bridge._visible_chat_ids = {"chat-a"}
        chats = [
            {"chat_id": "chat-a", "title": "Alpha"},
            {"chat_id": "chat-b", "title": "Beta"},
        ]
        with mock.patch.object(self.bridge, "_safe_chat_collection", return_value=(chats, None, True, None)):
            payload = self.bridge._get_chats_sync(50)

        self.assertEqual([chat["chat_id"] for chat in payload["chats"]], ["chat-a"])

    def test_get_chat_blocks_hidden_downstream_chats(self):
        self.bridge._visible_chat_ids = {"chat-a"}
        chats = [
            {"chat_id": "chat-a", "title": "Alpha"},
            {"chat_id": "chat-b", "title": "Beta"},
        ]
        with mock.patch.object(self.bridge, "_safe_chat_collection", return_value=(chats, None, True, None)):
            with self.assertRaises(IMessageBridgeError):
                self.bridge._get_chat_sync("chat-b", 20)

    def test_get_local_chat_allows_hidden_chats(self):
        chats = [
            {"chat_id": "chat-a", "title": "Alpha"},
            {"chat_id": "chat-b", "title": "Beta"},
        ]
        with mock.patch.object(self.bridge, "_safe_chat_collection", return_value=(chats, None, True, None)):
            with mock.patch.object(self.bridge, "_query_chat_messages", return_value=[{"id": "1", "text": "hello"}]):
                payload = self.bridge._get_chat_sync("chat-b", 20, False)

        self.assertEqual(payload["chat"]["chat_id"], "chat-b")
        self.assertEqual(payload["messages"][0]["text"], "hello")

    def test_set_chat_visibility_persists_selected_chat_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = IMessageBridge(visible_chats_path=Path(tmp) / "visible_chats.json")
            chats = [
                {"chat_id": "chat-a", "title": "Alpha"},
                {"chat_id": "chat-b", "title": "Beta"},
            ]
            with mock.patch.object(bridge, "_safe_chat_collection", return_value=(chats, None, True, None)):
                payload = bridge._set_chat_visibility_sync("chat-b", True)

            self.assertEqual(payload["visible_chat_ids"], ["chat-b"])
            self.assertEqual(bridge._visible_chat_ids, {"chat-b"})
            saved = bridge.visible_chats_path.read_text(encoding="utf-8")
            self.assertIn('"chat-b"', saved)
