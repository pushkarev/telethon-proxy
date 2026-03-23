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

    def test_from_env_loads_saved_mcp_settings_when_env_missing(self):
        secret_store = mock.Mock()
        secret_store.is_available = True
        secret_store.load_upstream_secrets.return_value = UpstreamSecrets()
        secret_store.load_or_create_mcp_token.return_value = ("mcp-token", False)

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "mcp_settings.json"
            settings_path.write_text('{"host": "100.92.237.54", "port": 8795, "scheme": "https"}\n', encoding="utf-8")

            with mock.patch("telegram_proxy.config.MacOSSecretStore", return_value=secret_store):
                with mock.patch.dict(
                    os.environ,
                    {"TP_MCP_SETTINGS": str(settings_path)},
                    clear=False,
                ):
                    config = ProxyConfig.from_env()

        self.assertEqual(config.mcp_host, "100.92.237.54")
        self.assertEqual(config.mcp_port, 8795)
        self.assertEqual(config.mcp_scheme, "https")

    def test_validate_mcp_tls_config_requires_cert_and_key_for_https(self):
        config = ProxyConfig(mcp_scheme="https")

        with self.assertRaisesRegex(ValueError, "TP_MCP_TLS_CERT and TP_MCP_TLS_KEY"):
            config.validate_mcp_tls_config()

    def test_from_env_defaults_imessage_to_disabled(self):
        secret_store = mock.Mock()
        secret_store.is_available = True
        secret_store.load_upstream_secrets.return_value = UpstreamSecrets()
        secret_store.load_or_create_mcp_token.return_value = ("mcp-token", False)

        with mock.patch("telegram_proxy.config.MacOSSecretStore", return_value=secret_store):
            with mock.patch("telegram_proxy.config.ProxyConfig._load_imessage_settings", return_value={}):
                with mock.patch.dict(os.environ, {}, clear=True):
                    config = ProxyConfig.from_env()

        self.assertFalse(config.imessage_enabled)

    def test_from_env_loads_saved_imessage_settings_when_env_missing(self):
        secret_store = mock.Mock()
        secret_store.is_available = True
        secret_store.load_upstream_secrets.return_value = UpstreamSecrets()
        secret_store.load_or_create_mcp_token.return_value = ("mcp-token", False)

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "imessage_settings.json"
            settings_path.write_text(
                '{"enabled": true, "messages_app_accessible": true, "database_accessible": false}\n',
                encoding="utf-8",
            )

            with mock.patch("telegram_proxy.config.MacOSSecretStore", return_value=secret_store):
                with mock.patch.dict(
                    os.environ,
                    {"TP_IMESSAGE_SETTINGS": str(settings_path)},
                    clear=True,
                ):
                    config = ProxyConfig.from_env()

        self.assertTrue(config.imessage_enabled)
        self.assertTrue(config.imessage_messages_app_accessible)
        self.assertFalse(config.imessage_database_accessible)

    def test_imessage_settings_path_uses_configured_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = ProxyConfig(imessage_settings_name=str(Path(tmp) / "imessage-settings.json"))

            self.assertEqual(config.imessage_settings_path, Path(tmp) / "imessage-settings.json")

    def test_imessage_visible_chats_path_uses_configured_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = ProxyConfig(imessage_visible_chats_name=str(Path(tmp) / "imessage-visible.json"))

            self.assertEqual(config.imessage_visible_chats_path, Path(tmp) / "imessage-visible.json")
