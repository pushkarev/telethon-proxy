import os
import unittest
from unittest import mock
from pathlib import Path

from telegram_proxy.whatsapp_bridge import resolve_node_bin


class WhatsAppBridgeTests(unittest.TestCase):
    def test_resolve_node_bin_prefers_explicit_existing_path(self):
        with mock.patch("telegram_proxy.whatsapp_bridge.Path.exists", return_value=True):
            self.assertEqual(resolve_node_bin("/custom/node"), "/custom/node")

    def test_resolve_node_bin_falls_back_to_homebrew_path(self):
        if not Path("/opt/homebrew/bin/node").exists():
            self.skipTest("Homebrew node is not installed at /opt/homebrew/bin/node on this host")
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("telegram_proxy.whatsapp_bridge.shutil.which", return_value=None):
                self.assertEqual(resolve_node_bin(None), "/opt/homebrew/bin/node")


if __name__ == "__main__":
    unittest.main()
