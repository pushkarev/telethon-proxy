import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv


API_ROOT = "https://api.telegram.org"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def telegram_request(token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    data = None
    if params is not None:
        encoded = urllib.parse.urlencode(params)
        data = encoded.encode("utf-8")

    request = urllib.request.Request(
        f"{API_ROOT}/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=70) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram API request failed: {exc}") from exc

    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")

    return payload["result"]


def main() -> None:
    load_dotenv()

    bot_token = require_env("TG_BOT_TOKEN")
    target_username = os.getenv("TG_BOT_USERNAME", "fewijhca3fih4bot")
    reply_text = os.getenv("TG_BOT_REPLY_TEXT", "ok")

    me = telegram_request(bot_token, "getMe")
    username = me.get("username", "<unknown>")
    print(f"Connected as @{username}")

    if username.lower() != target_username.lower():
        raise RuntimeError(
            f"Connected bot @{username} does not match expected @{target_username}. "
            "Set TG_BOT_USERNAME if you intend to use a different bot."
        )

    offset = 0
    print(f"Listening for messages for @{username}; replying with {reply_text!r}")

    while True:
        try:
            updates = telegram_request(
                bot_token,
                "getUpdates",
                {
                    "timeout": 60,
                    "offset": offset,
                    "allowed_updates": json.dumps(["message"]),
                },
            )

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat = message.get("chat", {})
                chat_id = chat.get("id")
                if chat_id is None:
                    continue

                incoming_text = message.get("text") or "<non-text message>"
                sender = message.get("from", {})
                sender_label = sender.get("username") or sender.get("first_name") or "unknown"
                print(f"Replying to chat {chat_id} from {sender_label}: {incoming_text}")

                telegram_request(
                    bot_token,
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": reply_text,
                        "reply_to_message_id": message.get("message_id"),
                    },
                )
        except KeyboardInterrupt:
            print("Stopped.")
            return
        except Exception as exc:
            print(f"Error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
