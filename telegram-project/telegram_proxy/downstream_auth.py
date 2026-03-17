from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import ProxyConfig


@dataclass(slots=True)
class PendingCode:
    phone: str
    code_hash: str
    expires_at: datetime


@dataclass(slots=True)
class DownstreamPrincipal:
    phone: str
    authenticated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class DownstreamAuthService:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self._pending: dict[str, PendingCode] = {}

    def send_code(self, phone: str, api_id: int, api_hash: str) -> dict:
        self._validate_api_credentials(api_id, api_hash)
        code_hash = secrets.token_hex(8)
        self._pending[phone] = PendingCode(
            phone=phone,
            code_hash=code_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        return {
            'phone': phone,
            'phone_code_hash': code_hash,
            'timeout': 600,
            'type': 'auth.sentCodeTypeApp',
            'next_type': None,
        }

    def sign_in(self, phone: str, code: str, phone_code_hash: str, password: str | None = None) -> DownstreamPrincipal:
        pending = self._pending.get(phone)
        if pending is None:
            raise PermissionError('No pending login for phone')
        if pending.code_hash != phone_code_hash:
            raise PermissionError('Invalid phone_code_hash')
        if pending.expires_at < datetime.now(timezone.utc):
            raise PermissionError('Login code expired')
        if code != self.config.downstream_login_code:
            raise PermissionError('Invalid proxy login code')
        if self.config.downstream_password and password != self.config.downstream_password:
            raise PermissionError('Invalid proxy password')
        self._pending.pop(phone, None)
        return DownstreamPrincipal(phone=phone)

    def _validate_api_credentials(self, api_id: int, api_hash: str) -> None:
        if api_id != self.config.downstream_api_id or api_hash != self.config.downstream_api_hash:
            raise PermissionError('Invalid downstream api credentials')
