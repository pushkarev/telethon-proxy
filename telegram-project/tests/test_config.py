import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telegram_proxy.config import ProxyConfig
from telegram_proxy.secrets_store import UpstreamSecrets


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

    def test_from_env_prefers_keychain_upstream_api_credentials(self):
        secret_store = mock.Mock()
        secret_store.is_available = True
        secret_store.load_upstream_secrets.return_value = UpstreamSecrets(
            api_id="12345",
            api_hash="keychain-hash",
        )
        secret_store.load_or_create_mcp_token.return_value = ("mcp-token", False)

        with mock.patch("telegram_proxy.config.MacOSSecretStore", return_value=secret_store):
            with mock.patch.dict(
                os.environ,
                {"TG_API_ID": "99999", "TG_API_HASH": "env-hash"},
                clear=False,
            ):
                config = ProxyConfig.from_env()

        self.assertEqual(config.upstream_api_id, 12345)
        self.assertEqual(config.upstream_api_hash, "keychain-hash")

    def test_from_env_uses_env_upstream_api_credentials_without_keychain_values(self):
        secret_store = mock.Mock()
        secret_store.is_available = True
        secret_store.load_upstream_secrets.return_value = UpstreamSecrets()
        secret_store.load_or_create_mcp_token.return_value = ("mcp-token", False)

        with mock.patch("telegram_proxy.config.MacOSSecretStore", return_value=secret_store):
            with mock.patch.dict(
                os.environ,
                {"TG_API_ID": "99999", "TG_API_HASH": "env-hash"},
                clear=False,
            ):
                config = ProxyConfig.from_env()

        self.assertEqual(config.upstream_api_id, 99999)
        self.assertEqual(config.upstream_api_hash, "env-hash")
