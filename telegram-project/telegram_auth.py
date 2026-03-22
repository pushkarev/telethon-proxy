from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass

from config_paths import DEFAULT_ENV_PATH


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


def persist_env_values(updates: dict[str, str]) -> None:
    path = DEFAULT_ENV_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        name, _ = line.split("=", 1)
        name = name.strip()
        if name in updates:
            rendered.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            rendered.append(line)

    for name, value in updates.items():
        if name not in seen:
            rendered.append(f"{name}={value}")

    path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")


def resolve_runtime_credentials(*, require_phone: bool) -> TelegramRuntimeCredentials:
    api_id_text, api_id_prompted = prompt_value("TG_API_ID", "Telegram API ID: ")
    api_hash, api_hash_prompted = prompt_value("TG_API_HASH", "Telegram API hash: ", secret=True)
    phone = os.getenv("TG_PHONE", "").strip()
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
        persist_env_values(updates)

    return TelegramRuntimeCredentials(api_id=int(api_id_text), api_hash=api_hash, phone=phone)
