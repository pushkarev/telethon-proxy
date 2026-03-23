import tempfile
import unittest
from pathlib import Path

from telegram_proxy.secrets_store import MacOSSecretStore


class _FakeSecretStore(MacOSSecretStore):
    def __init__(self, *, mcp_token: str = "") -> None:
        super().__init__(service="test.telethon-proxy.telegram")
        self._mcp_token = mcp_token

    @property
    def is_available(self) -> bool:
        return False

    def load_mcp_token(self) -> str:
        return self._mcp_token


class SecretStoreTests(unittest.TestCase):
    def test_load_or_create_mcp_token_deletes_legacy_file_when_keychain_token_exists(self):
        store = _FakeSecretStore(mcp_token="saved-token")

        with tempfile.TemporaryDirectory() as tmp:
            legacy_path = Path(tmp) / "mcp_token"
            legacy_path.write_text("legacy-token", encoding="utf-8")

            token, env_managed = store.load_or_create_mcp_token(legacy_path=legacy_path)

        self.assertEqual(token, "saved-token")
        self.assertFalse(env_managed)
        self.assertFalse(legacy_path.exists())

    def test_load_or_create_mcp_token_deletes_legacy_file_when_env_token_exists(self):
        store = _FakeSecretStore()

        with tempfile.TemporaryDirectory() as tmp:
            legacy_path = Path(tmp) / "mcp_token"
            legacy_path.write_text("legacy-token", encoding="utf-8")

            token, env_managed = store.load_or_create_mcp_token(env_token="env-token", legacy_path=legacy_path)

        self.assertEqual(token, "env-token")
        self.assertTrue(env_managed)
        self.assertFalse(legacy_path.exists())
