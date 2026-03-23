import tempfile
import unittest
from pathlib import Path

from telegram_proxy.config import ProxyConfig


class ProxyConfigTests(unittest.TestCase):
    def test_has_upstream_session_material_accepts_string_session(self):
        config = ProxyConfig(upstream_session_string="serialized-session")

        self.assertTrue(config.has_upstream_session_material())

    def test_has_upstream_session_material_accepts_file_backed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "proxy_upstream"
            Path(f"{base}.session").write_text("", encoding="utf-8")
            config = ProxyConfig(upstream_session_name=str(base))

            self.assertTrue(config.has_upstream_session_material())
