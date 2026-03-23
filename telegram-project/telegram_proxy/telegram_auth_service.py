from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

from .config import ProxyConfig
from .secrets_store import MacOSSecretStore


@dataclass(slots=True)
class PendingLogin:
    client: Any
    api_id: int
    api_hash: str
    phone: str
    phone_code_hash: str


class TelegramAuthService:
    def __init__(
        self,
        config: ProxyConfig,
        upstream,
        *,
        secret_store: MacOSSecretStore | None = None,
        client_factory: Callable[[object, int, str], Any] | None = None,
        session_serializer: Callable[[object], str] | None = None,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.secret_store = secret_store or MacOSSecretStore()
        self.client_factory = client_factory or (lambda session, api_id, api_hash: TelegramClient(session, api_id, api_hash))
        self.session_serializer = session_serializer or StringSession.save
        self._pending: PendingLogin | None = None
        self._needs_password = False
        self._last_error = ""

    async def close(self) -> None:
        pending = self._pending
        self._pending = None
        self._needs_password = False
        if pending is not None:
            await pending.client.disconnect()

    async def get_status(self) -> dict[str, object]:
        saved = self.secret_store.load_upstream_secrets() if self.secret_store.is_available else None
        has_api_credentials = bool(saved and saved.api_id and saved.api_hash)
        has_session = bool(saved and saved.session)
        if self._needs_password:
            next_step = "password"
        elif self._pending:
            next_step = "code"
        elif has_session:
            next_step = "ready"
        else:
            next_step = "credentials"
        return {
            "keychain_backend": "macOS Keychain" if self.secret_store.is_available else "Unavailable",
            "has_api_credentials": has_api_credentials,
            "has_session": has_session,
            "phone": saved.phone if saved and saved.phone else self.config.upstream_phone,
            "saved_phone": saved.phone if has_session and saved and saved.phone else None,
            "next_step": next_step,
            "pending_phone": self._pending.phone if self._pending else None,
            "last_error": self._last_error or None,
        }

    async def save_credentials(self, *, api_id: str, api_hash: str, phone: str) -> dict[str, object]:
        api_id_text = str(api_id).strip()
        if not api_id_text:
            raise ValueError("Telegram API ID is required")
        try:
            normalized_api_id = str(int(api_id_text))
        except ValueError as exc:
            raise ValueError("Telegram API ID must be a number") from exc
        normalized_api_hash = str(api_hash).strip()
        normalized_phone = str(phone).strip()
        if not normalized_api_hash:
            raise ValueError("Telegram API hash is required")

        previous = self.secret_store.load_upstream_secrets() if self.secret_store.is_available else None
        self.secret_store.save_upstream_credentials(
            api_id=normalized_api_id,
            api_hash=normalized_api_hash,
            phone=normalized_phone,
        )
        if previous and (previous.api_id != normalized_api_id or previous.api_hash != normalized_api_hash):
            self.secret_store.clear_upstream_session()

        self.config.upstream_api_id = int(normalized_api_id)
        self.config.upstream_api_hash = normalized_api_hash
        self.config.upstream_phone = normalized_phone
        self.config.upstream_session_string = ""
        self._last_error = ""
        await self.close()
        return await self.get_status()

    async def request_code(self, *, phone: str = "") -> dict[str, object]:
        await self.close()
        saved = self.secret_store.load_upstream_secrets() if self.secret_store.is_available else None
        api_id = int(saved.api_id if saved and saved.api_id else self.config.upstream_api_id)
        api_hash = saved.api_hash if saved and saved.api_hash else self.config.upstream_api_hash
        resolved_phone = str(phone).strip() or (saved.phone if saved else "") or self.config.upstream_phone
        if not api_id or not api_hash:
            raise ValueError("Save your Telegram API ID and API hash first")
        if not resolved_phone:
            raise ValueError("Telegram phone number is required before requesting a login code")

        client = self.client_factory(StringSession(""), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(resolved_phone)
        self._pending = PendingLogin(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            phone=resolved_phone,
            phone_code_hash=sent.phone_code_hash,
        )
        self._needs_password = False
        self._last_error = ""
        self.secret_store.save_upstream_credentials(api_id=str(api_id), api_hash=api_hash, phone=resolved_phone)
        self.config.upstream_phone = resolved_phone
        return await self.get_status()

    async def clear_saved_auth(self) -> dict[str, object]:
        await self.close()
        self.secret_store.clear_upstream_session()
        self.secret_store.clear_upstream_credentials()
        self._clear_local_session_files()
        await self.upstream.reset_authorization()
        self._last_error = ""
        return await self.get_status()

    async def clear_saved_session(self) -> dict[str, object]:
        await self.close()
        self.secret_store.clear_upstream_session()
        self._clear_local_session_files()
        self.config.upstream_session_string = ""
        await self.upstream.reset_session()
        self._last_error = ""
        return await self.get_status()

    async def submit_code(self, *, code: str) -> dict[str, object]:
        pending = self._require_pending()
        try:
            await pending.client.sign_in(
                phone=pending.phone,
                code=str(code).strip(),
                phone_code_hash=pending.phone_code_hash,
            )
        except SessionPasswordNeededError:
            self._needs_password = True
            self._last_error = ""
            return await self.get_status()
        return await self._complete_login()

    async def submit_password(self, *, password: str) -> dict[str, object]:
        pending = self._require_pending()
        await pending.client.sign_in(password=password)
        return await self._complete_login()

    def _require_pending(self) -> PendingLogin:
        if self._pending is None:
            raise ValueError("Request a Telegram login code first")
        return self._pending

    async def _complete_login(self) -> dict[str, object]:
        pending = self._require_pending()
        me = await pending.client.get_me()
        session_string = self.session_serializer(pending.client.session)
        phone = getattr(me, "phone", None) or pending.phone
        self.secret_store.save_upstream_credentials(
            api_id=str(pending.api_id),
            api_hash=pending.api_hash,
            phone=phone,
        )
        self.secret_store.save_upstream_session(session_string)
        self.config.upstream_api_id = pending.api_id
        self.config.upstream_api_hash = pending.api_hash
        self.config.upstream_phone = phone
        self.config.upstream_session_string = session_string
        await self.upstream.apply_authorized_session(
            api_id=pending.api_id,
            api_hash=pending.api_hash,
            phone=phone,
            session_string=session_string,
        )
        await pending.client.disconnect()
        self._pending = None
        self._needs_password = False
        self._last_error = ""
        status = await self.get_status()
        status["account"] = {
            "id": getattr(me, "id", None),
            "name": " ".join(
                part for part in [getattr(me, "first_name", None), getattr(me, "last_name", None)] if part
            ).strip()
            or getattr(me, "username", None)
            or "Telegram account",
            "username": getattr(me, "username", None),
            "phone": phone,
        }
        status["next_step"] = "ready"
        return status

    def _clear_local_session_files(self) -> None:
        base = self.config.upstream_session_path
        candidates = [
            base,
            Path(f"{base}.session"),
            Path(f"{base}.session-journal"),
            Path(f"{base}.session-shm"),
            Path(f"{base}.session-wal"),
        ]
        for path in candidates:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
