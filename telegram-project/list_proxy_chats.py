from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys

from config_paths import load_project_env
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telegram_proxy.hooks import IncomingHook


logger = logging.getLogger(__name__)


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
    parser.add_argument("--hook-command", default=os.getenv("TP_INCOMING_HOOK", ""))
    parser.add_argument("--list-only", action="store_true", help="List chats and exit without watching for messages.")
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


def display_name(entity: object | None) -> str:
    if entity is None:
        return ""
    for attribute in ("title", "username", "first_name"):
        value = getattr(entity, attribute, None)
        if value:
            return str(value)
    if getattr(entity, "last_name", None):
        return str(entity.last_name)
    return ""


def echo_text_for_event(event: events.NewMessage.Event) -> str:
    text = (event.raw_text or "").strip()
    if text:
        return text
    if getattr(event, "sticker", False):
        return "[sticker]"
    if getattr(event, "photo", False):
        return "[photo]"
    if getattr(event, "gif", False):
        return "[gif]"
    if getattr(event, "voice", False):
        return "[voice]"
    if getattr(event, "video", False):
        return "[video]"
    file_name = getattr(getattr(event, "file", None), "name", "") or ""
    if file_name:
        return f"[file] {file_name}"
    if event.message.media is not None:
        return "[media]"
    return "[unsupported message]"


def hook_payload_for_event(event: events.NewMessage.Event, *, echoed_text: str) -> dict[str, object]:
    sender = getattr(event, "sender", None)
    chat = getattr(event, "chat", None)
    return {
        "chat_id": event.chat_id,
        "message_id": event.id,
        "sender_id": event.sender_id,
        "text": event.raw_text,
        "echo_text": echoed_text,
        "date": event.message.date.isoformat() if event.message.date else None,
        "is_private": event.is_private,
        "is_group": event.is_group,
        "is_channel": event.is_channel,
        "has_media": event.message.media is not None,
        "chat_name": display_name(chat),
        "sender_name": display_name(sender),
    }


async def list_dialogs(client: TelegramClient, *, limit: int) -> None:
    async for dialog in client.iter_dialogs(limit=limit):
        entity = dialog.entity
        username = getattr(entity, "username", "") or "-"
        print(f"{dialog.id}\t{dialog.name}\t{username}")


async def watch_and_echo(client: TelegramClient, *, hook: IncomingHook) -> None:
    @client.on(events.NewMessage(incoming=True))
    async def on_message(event: events.NewMessage.Event) -> None:
        echoed_text = echo_text_for_event(event)
        payload = hook_payload_for_event(event, echoed_text=echoed_text)
        delivery = await hook.deliver(payload)
        logger.info(
            "incoming chat_id=%s message_id=%s hook_delivered=%s hook_rc=%s payload=%s",
            event.chat_id,
            event.id,
            delivery.delivered,
            delivery.returncode,
            json.dumps(payload, ensure_ascii=False),
        )
        await client.send_read_acknowledge(event.chat_id, max_id=event.id)
        await client.send_message(event.chat_id, echoed_text)

    print("")
    print("Watching for incoming messages. Press Ctrl+C to stop.")
    await client.run_until_disconnected()


async def amain() -> None:
    load_project_env()
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    session_string = require_value("TP_PROXY_SESSION", args.session, "Proxy session string: ", secret=True)
    phone = require_value("TP_PROXY_PHONE", args.phone, "Proxy phone number: ")
    password = args.password.strip()
    code = args.code.strip()
    hook = IncomingHook(args.hook_command)

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
        await list_dialogs(client, limit=args.limit)
        if not args.list_only:
            await watch_and_echo(client, hook=hook)
    finally:
        await client.disconnect()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("")
        print("Stopped.")


if __name__ == "__main__":
    main()
