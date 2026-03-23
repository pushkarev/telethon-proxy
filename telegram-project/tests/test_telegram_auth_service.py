import unittest
from types import SimpleNamespace

from telethon.errors import SessionPasswordNeededError

from telegram_proxy.config import ProxyConfig
from telegram_proxy.secrets_store import UpstreamSecrets
from telegram_proxy.telegram_auth_service import TelegramAuthService


class _FakeSecretStore:
    def __init__(self) -> None:
        self.is_available = True
        self.secrets = UpstreamSecrets()

    def load_upstream_secrets(self) -> UpstreamSecrets:
        return self.secrets

    def save_upstream_credentials(self, *, api_id: str, api_hash: str, phone: str = "") -> None:
        self.secrets = UpstreamSecrets(api_id=api_id, api_hash=api_hash, phone=phone, session=self.secrets.session)

    def save_upstream_session(self, session: str) -> None:
        self.secrets = UpstreamSecrets(
            api_id=self.secrets.api_id,
            api_hash=self.secrets.api_hash,
            phone=self.secrets.phone,
            session=session,
        )

    def clear_upstream_session(self) -> None:
        self.secrets = UpstreamSecrets(
            api_id=self.secrets.api_id,
            api_hash=self.secrets.api_hash,
            phone=self.secrets.phone,
            session="",
        )

    def clear_upstream_credentials(self) -> None:
        self.secrets = UpstreamSecrets(session=self.secrets.session)


class _FakeUpstream:
    def __init__(self, config=None) -> None:
        self.applied = []
        self.reset_calls = 0
        self.reset_session_calls = 0
        self.config = config

    async def apply_authorized_session(self, *, api_id: int, api_hash: str, phone: str, session_string: str) -> None:
        self.applied.append(
            {
                "api_id": api_id,
                "api_hash": api_hash,
                "phone": phone,
                "session_string": session_string,
            }
        )

    async def reset_authorization(self) -> None:
        self.reset_calls += 1
        if self.config is not None:
            self.config.upstream_api_id = 0
            self.config.upstream_api_hash = ""
            self.config.upstream_phone = ""
            self.config.upstream_session_string = ""

    async def reset_session(self) -> None:
        self.reset_session_calls += 1
        if self.config is not None:
            self.config.upstream_session_string = ""


class _FakeClient:
    def __init__(self, require_password: bool) -> None:
        self.require_password = require_password
        self.connected = False
        self.session = object()

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def send_code_request(self, phone: str):
        return SimpleNamespace(phone_code_hash=f"hash-for-{phone}")

    async def sign_in(self, **kwargs):
        if "password" in kwargs:
            self.require_password = False
            return
        if self.require_password:
            raise SessionPasswordNeededError(request=None)

    async def get_me(self):
        return SimpleNamespace(id=1000, first_name="Dmitry", last_name="Pushkarev", username="dimapush", phone="+15550000000")


class TelegramAuthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_credentials_clears_existing_session_when_keys_change(self):
        config = ProxyConfig()
        upstream = _FakeUpstream(config)
        secret_store = _FakeSecretStore()
        secret_store.secrets = UpstreamSecrets(api_id="1", api_hash="old", phone="+1", session="persisted-session")
        service = TelegramAuthService(config, upstream, secret_store=secret_store)

        status = await service.save_credentials(api_id="2", api_hash="new-secret", phone="+2")

        self.assertTrue(status["has_api_credentials"])
        self.assertFalse(status["has_session"])
        self.assertEqual(secret_store.secrets.session, "")
        self.assertEqual(config.upstream_api_id, 2)

    async def test_password_flow_saves_session_and_updates_upstream(self):
        config = ProxyConfig()
        upstream = _FakeUpstream(config)
        secret_store = _FakeSecretStore()
        created_clients = []

        def client_factory(_session, _api_id, _api_hash):
            client = _FakeClient(require_password=True)
            created_clients.append(client)
            return client

        service = TelegramAuthService(
            config,
            upstream,
            secret_store=secret_store,
            client_factory=client_factory,
            session_serializer=lambda _session: "serialized-session",
        )

        await service.save_credentials(api_id="12345", api_hash="hash", phone="+15550000000")
        requested = await service.request_code()
        self.assertEqual(requested["next_step"], "code")

        password_step = await service.submit_code(code="11111")
        self.assertEqual(password_step["next_step"], "password")

        finished = await service.submit_password(password="correct horse battery staple")
        self.assertEqual(finished["next_step"], "ready")
        self.assertTrue(finished["has_session"])
        self.assertEqual(secret_store.secrets.session, "serialized-session")
        self.assertEqual(upstream.applied[-1]["session_string"], "serialized-session")
        self.assertFalse(created_clients[-1].connected)

    async def test_clear_saved_auth_removes_credentials_and_session(self):
        config = ProxyConfig(upstream_api_id=12345, upstream_api_hash="hash", upstream_phone="+15550000000")
        upstream = _FakeUpstream(config)
        secret_store = _FakeSecretStore()
        secret_store.secrets = UpstreamSecrets(
            api_id="12345",
            api_hash="hash",
            phone="+15550000000",
            session="serialized-session",
        )
        service = TelegramAuthService(config, upstream, secret_store=secret_store)

        status = await service.clear_saved_auth()

        self.assertFalse(status["has_api_credentials"])
        self.assertFalse(status["has_session"])
        self.assertEqual(secret_store.secrets.api_id, "")
        self.assertEqual(secret_store.secrets.session, "")
        self.assertEqual(config.upstream_api_id, 0)
        self.assertEqual(upstream.reset_calls, 1)

    async def test_clear_saved_session_keeps_credentials_and_phone(self):
        config = ProxyConfig(upstream_api_id=12345, upstream_api_hash="hash", upstream_phone="+15550000000")
        upstream = _FakeUpstream(config)
        secret_store = _FakeSecretStore()
        secret_store.secrets = UpstreamSecrets(
            api_id="12345",
            api_hash="hash",
            phone="+15550000000",
            session="serialized-session",
        )
        service = TelegramAuthService(config, upstream, secret_store=secret_store)

        status = await service.clear_saved_session()

        self.assertTrue(status["has_api_credentials"])
        self.assertFalse(status["has_session"])
        self.assertEqual(status["phone"], "+15550000000")
        self.assertIsNone(status["saved_phone"])
        self.assertEqual(status["next_step"], "credentials")
        self.assertEqual(secret_store.secrets.api_id, "12345")
        self.assertEqual(secret_store.secrets.session, "")
        self.assertEqual(config.upstream_api_id, 12345)
        self.assertEqual(config.upstream_session_string, "")
        self.assertEqual(upstream.reset_session_calls, 1)
