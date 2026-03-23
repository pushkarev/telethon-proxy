from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass

from telegram_proxy.secrets_store import (
    MacOSSecretStore,
    UPSTREAM_API_HASH_ACCOUNT,
    UPSTREAM_API_ID_ACCOUNT,
    UPSTREAM_PHONE_ACCOUNT,
)


@dataclass(slots=True)
class TelegramRuntimeCredentials:
    api_id: int
    api_hash: str
    phone: str = ""


def prompt_value(name: str, prompt: str, *, secret: bool = False) -> tuple[str, bool]:
    value = os.getenv(name)
    if value:
        return value, False
    if not sys.stdin.isatty():
        raise RuntimeError(f"Missing required value: {name}")
    if secret:
        value = getpass.getpass(prompt)
    else:
        value = input(prompt).strip()
    if not value:
        raise RuntimeError(f"Missing required value: {name}")
    return value, True


def persist_runtime_values(updates: dict[str, str]) -> None:
    secret_store = MacOSSecretStore()
    if not secret_store.is_available:
        raise RuntimeError("Telegram secrets require macOS Keychain on this build")
    secret_store.save_upstream_credentials(
        api_id=updates.get("TG_API_ID", secret_store.get(UPSTREAM_API_ID_ACCOUNT) or ""),
        api_hash=updates.get("TG_API_HASH", secret_store.get(UPSTREAM_API_HASH_ACCOUNT) or ""),
        phone=updates.get("TG_PHONE", secret_store.get(UPSTREAM_PHONE_ACCOUNT) or ""),
    )


def load_saved_session_string() -> str:
    secret_store = MacOSSecretStore()
    if not secret_store.is_available:
        return ""
    return secret_store.load_upstream_secrets().session


def persist_session_string(session_string: str) -> None:
    secret_store = MacOSSecretStore()
    if secret_store.is_available:
        secret_store.save_upstream_session(session_string)


def resolve_runtime_credentials(*, require_phone: bool) -> TelegramRuntimeCredentials:
    secret_store = MacOSSecretStore()
    saved = secret_store.load_upstream_secrets() if secret_store.is_available else None

    api_id_text = os.getenv("TG_API_ID", saved.api_id if saved else "").strip()
    api_id_prompted = False
    if not api_id_text:
        api_id_text, api_id_prompted = prompt_value("TG_API_ID", "Telegram API ID: ")

    api_hash = os.getenv("TG_API_HASH", saved.api_hash if saved else "").strip()
    api_hash_prompted = False
    if not api_hash:
        api_hash, api_hash_prompted = prompt_value("TG_API_HASH", "Telegram API hash: ", secret=True)

    phone = os.getenv("TG_PHONE", saved.phone if saved else "").strip()
    phone_prompted = False
    if require_phone and not phone:
        phone, phone_prompted = prompt_value("TG_PHONE", "Telegram phone number: ")

    updates: dict[str, str] = {}
    if api_id_prompted:
        updates["TG_API_ID"] = api_id_text
    if api_hash_prompted:
        updates["TG_API_HASH"] = api_hash
    if phone_prompted:
        updates["TG_PHONE"] = phone
    if updates:
        persist_runtime_values(updates)

    return TelegramRuntimeCredentials(api_id=int(api_id_text), api_hash=api_hash, phone=phone)
