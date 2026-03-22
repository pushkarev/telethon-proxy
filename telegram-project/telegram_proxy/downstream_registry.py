from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from telethon.crypto import AuthKey
from telethon.sessions import StringSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RegisteredClient:
    key_id: int
    auth_key: AuthKey
    label: str
    created_at: datetime
    phone: str | None = None
    authenticated_at: datetime | None = None


@dataclass(slots=True)
class IssuedDownstreamSession:
    key_id: int
    session_string: str
    label: str


class DownstreamRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def issue_session(self, *, label: str, host: str, port: int, dc_id: int = 2) -> IssuedDownstreamSession:
        auth_key = AuthKey(os.urandom(256))
        created_at = _utcnow()
        client = RegisteredClient(
            key_id=auth_key.key_id,
            auth_key=auth_key,
            label=label,
            created_at=created_at,
        )
        with self._lock:
            payload = self._load_unlocked()
            payload["clients"][str(client.key_id)] = self._serialize_client(client)
            self._save_unlocked(payload)

        session = StringSession()
        session.set_dc(dc_id, host, port)
        session.auth_key = auth_key
        return IssuedDownstreamSession(
            key_id=client.key_id,
            session_string=session.save(),
            label=label,
        )

    def get_client(self, key_id: int) -> RegisteredClient | None:
        with self._lock:
            payload = self._load_unlocked()
            raw = payload["clients"].get(str(key_id))
        if raw is None:
            return None
        return self._deserialize_client(key_id, raw)

    def mark_authenticated(self, key_id: int, *, phone: str) -> RegisteredClient:
        with self._lock:
            payload = self._load_unlocked()
            raw = payload["clients"].get(str(key_id))
            if raw is None:
                raise KeyError(f"unknown downstream auth key id: {key_id}")
            raw["phone"] = phone
            raw["authenticated_at"] = _utcnow().isoformat()
            payload["clients"][str(key_id)] = raw
            self._save_unlocked(payload)
        return self._deserialize_client(key_id, raw)

    def default_label(self) -> str:
        return "proxy-client"

    def _load_unlocked(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "clients": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save_unlocked(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=self.path.parent) as tmp:
            json.dump(payload, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    def _serialize_client(self, client: RegisteredClient) -> dict:
        return {
            "auth_key": base64.b64encode(client.auth_key.key).decode("ascii"),
            "label": client.label,
            "created_at": client.created_at.isoformat(),
            "phone": client.phone,
            "authenticated_at": client.authenticated_at.isoformat() if client.authenticated_at else None,
        }

    def _deserialize_client(self, key_id: int, raw: dict) -> RegisteredClient:
        return RegisteredClient(
            key_id=key_id,
            auth_key=AuthKey(base64.b64decode(raw["auth_key"])),
            label=raw.get("label") or self.default_label(),
            created_at=datetime.fromisoformat(raw["created_at"]),
            phone=raw.get("phone"),
            authenticated_at=datetime.fromisoformat(raw["authenticated_at"]) if raw.get("authenticated_at") else None,
        )
