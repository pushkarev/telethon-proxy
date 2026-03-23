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
        description="Monitor all Cloud chats through the local MCP endpoint and echo incoming messages back."
    )
    parser.add_argument("--host", default=config.mcp_host)
    parser.add_argument("--port", type=int, default=config.mcp_port)
    parser.add_argument("--path", default=config.mcp_path)
    parser.add_argument("--token", default=config.mcp_token)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between MCP update polls.")
    parser.add_argument("--update-limit", type=int, default=100, help="Maximum recent MCP updates to fetch each poll.")
    parser.add_argument("--replay-existing", action="store_true", help="Echo updates already present in the MCP buffer.")
    parser.add_argument("--client-name", default="echo-mcp-chats")
    parser.add_argument("--protocol-version", default=SERVER_PROTOCOL_VERSION)
    return parser.parse_args()


def update_key(update: dict[str, Any]) -> tuple[str, str | None, int | None]:
    kind = str(update.get("kind") or "")
    peer_id = update.get("peer_id")
    message_id = update.get("message_id")
    return kind, str(peer_id) if peer_id is not None else None, int(message_id) if message_id is not None else None


def echo_text_for_message(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return "[unsupported message]"
    text = str(message.get("text") or "").strip()
    if text:
        return text
    media = str(message.get("media") or "").strip()
    if media:
        return f"[{media}]"
    return "[unsupported message]"


def should_echo_update(update: dict[str, Any], seen: set[tuple[str, str | None, int | None]]) -> bool:
    key = update_key(update)
    if key in seen:
        return False
    if update.get("kind") != "new_message":
        return False
    if not update.get("incoming"):
        return False
    if key[1] is None or key[2] is None:
        return False
    return True


def fetch_updates(client: McpClient, *, limit: int) -> list[dict[str, Any]]:
    payload = client.call_tool("telegram.get_updates", {"limit": limit})
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise McpClientError("MCP server returned invalid updates payload")
    return [item for item in updates if isinstance(item, dict)]


def monitor_and_echo(client: McpClient, *, poll_interval: float, update_limit: int, replay_existing: bool) -> None:
    chats = client.list_chats(limit=500)
    print("Monitoring chats:")
    for chat in chats:
        print(f"{chat['peer_id']}\t{chat['title']}\t{chat.get('username') or '-'}")
    print("")

    seen = {update_key(update) for update in fetch_updates(client, limit=update_limit)}
    if replay_existing:
        seen.clear()
    print("Watching for new incoming messages. Press Ctrl+C to stop.")

    while True:
        updates = fetch_updates(client, limit=update_limit)
        updates.sort(key=lambda item: (str(item.get("date") or ""), int(item.get("message_id") or 0)))
        for update in updates:
            key = update_key(update)
            if key in seen:
                continue
            seen.add(key)
            if not should_echo_update(update, seen=set()):
                continue

            peer_id = str(update["peer_id"])
            message_id = int(update["message_id"])
            message = update.get("message")
            text = echo_text_for_message(message if isinstance(message, dict) else None)
            client.call_tool(
                "telegram.send_message",
                {
                    "peer": peer_id,
                    "text": text,
                    "reply_to_message_id": message_id,
                },
            )
            client.call_tool(
                "telegram.mark_read",
                {
                    "peer": peer_id,
                    "max_id": message_id,
                },
            )
            print(
                json.dumps(
                    {
                        "peer_id": peer_id,
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
