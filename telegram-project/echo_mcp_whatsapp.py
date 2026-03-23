from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from config_paths import load_project_env
from list_mcp_chats import McpClient, McpClientError
from telegram_proxy.config import ProxyConfig
from telegram_proxy.mcp_service import SERVER_PROTOCOL_VERSION


def parse_args(config: ProxyConfig) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor all Cloud-labeled WhatsApp chats through the local MCP endpoint and echo incoming messages back."
    )
    parser.add_argument("--host", default=config.mcp_host)
    parser.add_argument("--port", type=int, default=config.mcp_port)
    parser.add_argument("--path", default=config.mcp_path)
    parser.add_argument("--token", default=config.mcp_token)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between MCP update polls.")
    parser.add_argument("--update-limit", type=int, default=100, help="Maximum recent MCP updates to fetch each poll.")
    parser.add_argument("--replay-existing", action="store_true", help="Echo updates already present in the MCP buffer.")
    parser.add_argument("--client-name", default="echo-mcp-whatsapp")
    parser.add_argument("--protocol-version", default=SERVER_PROTOCOL_VERSION)
    return parser.parse_args()


def update_key(update: dict[str, Any]) -> tuple[str, str | None, str | None]:
    kind = str(update.get("kind") or "")
    chat_id = update.get("chat_id")
    message_id = update.get("message_id")
    return (
        kind,
        str(chat_id) if chat_id is not None else None,
        str(message_id) if message_id is not None else None,
    )


def echo_text_for_message(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return "[unsupported message]"
    text = str(message.get("text") or "").strip()
    if text:
        return text
    kind = str(message.get("kind") or "").strip()
    if kind:
        return f"[{kind}]"
    return "[unsupported message]"


def should_echo_update(update: dict[str, Any], seen: set[tuple[str, str | None, str | None]]) -> bool:
    key = update_key(update)
    if key in seen:
        return False
    if update.get("kind") != "new_message":
        return False
    if key[1] is None or key[2] is None:
        return False
    message = update.get("message")
    if not isinstance(message, dict):
        return False
    if bool(message.get("from_me")):
        return False
    return True


def fetch_updates(client: McpClient, *, limit: int) -> list[dict[str, Any]]:
    payload = client.call_tool("whatsapp.get_updates", {"limit": limit})
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise McpClientError("MCP server returned invalid WhatsApp updates payload")
    return [item for item in updates if isinstance(item, dict)]


def monitor_and_echo(client: McpClient, *, poll_interval: float, update_limit: int, replay_existing: bool) -> None:
    payload = client.call_tool("whatsapp.list_chats", {"limit": 500})
    chats = payload.get("chats")
    if not isinstance(chats, list):
        raise McpClientError("MCP server returned an invalid WhatsApp chat list")

    print("Monitoring WhatsApp chats:")
    for chat in chats:
        jid = str(chat.get("jid") or "")
        kind = str(chat.get("kind") or "")
        title = str(chat.get("title") or jid)
        print(f"{jid}\t{kind}\t{title}")
    print("")

    seen = {update_key(update) for update in fetch_updates(client, limit=update_limit)}
    if replay_existing:
        seen.clear()
    print("Watching for new incoming WhatsApp messages. Press Ctrl+C to stop.")

    while True:
        updates = fetch_updates(client, limit=update_limit)
        updates.sort(key=lambda item: (str(item.get("message", {}).get("date") or ""), str(item.get("message_id") or "")))
        for update in updates:
            key = update_key(update)
            if key in seen:
                continue
            seen.add(key)
            if not should_echo_update(update, seen=set()):
                continue

            chat_id = str(update["chat_id"])
            message_id = str(update["message_id"])
            message = update.get("message")
            text = echo_text_for_message(message if isinstance(message, dict) else None)
            client.call_tool(
                "whatsapp.mark_read",
                {
                    "jid": chat_id,
                    "message_id": message_id,
                },
            )
            client.call_tool(
                "whatsapp.send_message",
                {
                    "jid": chat_id,
                    "text": text,
                },
            )
            print(
                json.dumps(
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "echo_text": text,
                    },
                    ensure_ascii=False,
                )
            )
            sys.stdout.flush()
        time.sleep(poll_interval)


def main() -> int:
    load_project_env()
    config = ProxyConfig.from_env()
    args = parse_args(config)
    client = McpClient(
        host=args.host,
        port=args.port,
        path=args.path,
        token=args.token,
        timeout=args.timeout,
    )
    try:
        client.initialize(client_name=args.client_name, protocol_version=args.protocol_version)
        client.notify_initialized()
        monitor_and_echo(
            client,
            poll_interval=args.poll_interval,
            update_limit=args.update_limit,
            replay_existing=args.replay_existing,
        )
    except KeyboardInterrupt:
        print("")
        print("Stopped.")
        return 0
    except McpClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
