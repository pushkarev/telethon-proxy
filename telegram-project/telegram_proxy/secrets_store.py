from __future__ import annotations

import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


KEYCHAIN_SERVICE = "dev.telethon-proxy.telegram"
UPSTREAM_API_ID_ACCOUNT = "upstream_api_id"
UPSTREAM_API_HASH_ACCOUNT = "upstream_api_hash"
UPSTREAM_PHONE_ACCOUNT = "upstream_phone"
UPSTREAM_SESSION_ACCOUNT = "upstream_session"
MCP_TOKEN_ACCOUNT = "mcp_token"


class SecretStoreError(RuntimeError):
    pass


@dataclass(slots=True)
class UpstreamSecrets:
    api_id: str = ""
    api_hash: str = ""
    phone: str = ""
    session: str = ""


class MacOSSecretStore:
    def __init__(self, *, service: str = KEYCHAIN_SERVICE) -> None:
        self.service = service

    @property
    def is_available(self) -> bool:
        return sys.platform == "darwin"

    def get(self, account: str) -> str | None:
        if not self.is_available:
            return None
        result = subprocess.run(
            ["security", "find-generic-password", "-s", self.service, "-a", account, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.rstrip("\n")
        stderr = (result.stderr or "").strip()
        if "could not be found" in stderr.lower():
            return None
        raise SecretStoreError(stderr or f"security find-generic-password failed with {result.returncode}")

    def set(self, account: str, value: str) -> None:
        if not self.is_available:
            raise SecretStoreError("macOS Keychain is only available on macOS")
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", self.service, "-a", account, "-w", value],
            check=True,
            capture_output=True,
            text=True,
        )

    def delete(self, account: str) -> None:
        if not self.is_available:
            return
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", self.service, "-a", account],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        stderr = (result.stderr or "").strip()
        if "could not be found" in stderr.lower():
            return
        raise SecretStoreError(stderr or f"security delete-generic-password failed with {result.returncode}")

    def load_upstream_secrets(self) -> UpstreamSecrets:
        return UpstreamSecrets(
            api_id=self.get(UPSTREAM_API_ID_ACCOUNT) or "",
            api_hash=self.get(UPSTREAM_API_HASH_ACCOUNT) or "",
            phone=self.get(UPSTREAM_PHONE_ACCOUNT) or "",
            session=self.get(UPSTREAM_SESSION_ACCOUNT) or "",
        )

    def save_upstream_credentials(self, *, api_id: str, api_hash: str, phone: str = "") -> None:
        self.set(UPSTREAM_API_ID_ACCOUNT, api_id)
        self.set(UPSTREAM_API_HASH_ACCOUNT, api_hash)
        if phone:
            self.set(UPSTREAM_PHONE_ACCOUNT, phone)
        else:
            self.delete(UPSTREAM_PHONE_ACCOUNT)

    def save_upstream_session(self, session: str) -> None:
        self.set(UPSTREAM_SESSION_ACCOUNT, session)

    def clear_upstream_session(self) -> None:
        self.delete(UPSTREAM_SESSION_ACCOUNT)

    def clear_upstream_credentials(self) -> None:
        self.delete(UPSTREAM_API_ID_ACCOUNT)
        self.delete(UPSTREAM_API_HASH_ACCOUNT)
        self.delete(UPSTREAM_PHONE_ACCOUNT)

    def load_mcp_token(self) -> str:
        return self.get(MCP_TOKEN_ACCOUNT) or ""

    def save_mcp_token(self, token: str) -> None:
        self.set(MCP_TOKEN_ACCOUNT, token)

    def clear_mcp_token(self) -> None:
        self.delete(MCP_TOKEN_ACCOUNT)

    def load_or_create_mcp_token(self, *, env_token: str = "", legacy_path: Path | None = None) -> tuple[str, bool]:
        env_token = env_token.strip()
        if env_token:
            self._delete_legacy_file(legacy_path)
            return env_token, True

        token = self.load_mcp_token()
        if token:
            self._delete_legacy_file(legacy_path)
            return token, False

        token = self._migrate_mcp_token_from_file(legacy_path)
        if token:
            return token, False

        token = secrets.token_urlsafe(32)
        if self.is_available:
            self.save_mcp_token(token)
        return token, False

    def rotate_mcp_token(self) -> str:
        if not self.is_available:
            raise SecretStoreError("MCP token rotation requires macOS Keychain")
        token = secrets.token_urlsafe(32)
        self.save_mcp_token(token)
        return token

    def _migrate_mcp_token_from_file(self, legacy_path: Path | None) -> str:
        if legacy_path is None:
            return ""
        path = legacy_path.expanduser()
        if not path.exists():
            return ""
        token = path.read_text(encoding="utf-8").strip()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        if not token:
            return ""
        if self.is_available:
            self.save_mcp_token(token)
        return token

    def _delete_legacy_file(self, legacy_path: Path | None) -> None:
        if legacy_path is None:
            return
        path = legacy_path.expanduser()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
