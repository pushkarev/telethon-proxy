from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from config_paths import load_project_env
from telethon import TelegramClient
from telethon.sessions import StringSession


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to the local Telegram-compatible proxy with Telethon and list chats."
    )
    parser.add_argument("--host", default=os.getenv("TP_MTPROTO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TP_MTPROTO_PORT", "9001")))
    parser.add_argument("--api-id", type=int, default=_env_int("TP_DOWNSTREAM_API_ID", 900000))
    parser.add_argument("--api-hash", default=os.getenv("TP_DOWNSTREAM_API_HASH", "dev-proxy-change-me"))
    parser.add_argument("--session", default=os.getenv("TP_PROXY_SESSION", ""))
    parser.add_argument("--phone", default=os.getenv("TP_PROXY_PHONE", ""))
    parser.add_argument("--code", default=os.getenv("TP_PROXY_CODE", ""))
    parser.add_argument("--password", default=os.getenv("TP_PROXY_PASSWORD", ""))
    parser.add_argument("--limit", type=int, default=int(os.getenv("TP_DIALOG_LIMIT", "100")))
    return parser.parse_args()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def require_value(name: str, value: str, prompt: str, *, secret: bool = False) -> str:
    value = value.strip()
    if value:
        return value
    if not sys.stdin.isatty():
        raise RuntimeError(f"Missing required value: {name}")
    if secret:
        value = getpass.getpass(prompt).strip()
    else:
        value = input(prompt).strip()
    if not value:
        raise RuntimeError(f"Missing required value: {name}")
    return value


def build_session(session_string: str, host: str, port: int) -> StringSession:
    session = StringSession(session_string)
    dc_id = session.dc_id or 2
    session.set_dc(dc_id, host, port)
    return session


async def amain() -> None:
    load_project_env()
    args = parse_args()

    session_string = require_value("TP_PROXY_SESSION", args.session, "Proxy session string: ", secret=True)
    phone = require_value("TP_PROXY_PHONE", args.phone, "Proxy phone number: ")
    password = args.password.strip()
    code = args.code.strip()

    client = TelegramClient(
        build_session(session_string, args.host, args.port),
        args.api_id,
        args.api_hash,
        receive_updates=False,
    )

    await client.connect()
    try:
        if not await client.is_user_authorized():
            start_kwargs: dict[str, object] = {"phone": phone}
            if code:
                start_kwargs["code_callback"] = lambda: code
            if password:
                start_kwargs["password"] = password
            await client.start(**start_kwargs)

        me = await client.get_me()
        print(f"Authorized as: {getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip())
        print(f"Phone: +{me.phone}" if getattr(me, "phone", None) else "Phone: <unknown>")
        print("")

        async for dialog in client.iter_dialogs(limit=args.limit):
            entity = dialog.entity
            username = getattr(entity, "username", "") or "-"
            print(f"{dialog.id}\t{dialog.name}\t{username}")
    finally:
        await client.disconnect()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
