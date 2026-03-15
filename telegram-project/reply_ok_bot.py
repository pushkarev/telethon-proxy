import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


API_ROOT = "https://api.telegram.org"
DEFAULT_HOOK_PATH = "/home/ubuntu/incoming_hook.sh"


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


def run_hook(hook_path: str, update: dict[str, Any]) -> None:
    hook = Path(hook_path)
    if not hook.exists():
        raise RuntimeError(f"Hook script does not exist: {hook}")

    payload = json.dumps(update, ensure_ascii=False)
    subprocess.run(
        [str(hook)],
        input=payload,
        text=True,
        check=True,
        capture_output=True,
    )


def main() -> None:
    load_dotenv()

    bot_token = require_env("TG_BOT_TOKEN")
    target_username = os.getenv("TG_BOT_USERNAME", "fewijhca3fih4bot")
    hook_path = os.getenv("TG_BOT_HOOK_PATH", DEFAULT_HOOK_PATH)

    me = telegram_request(bot_token, "getMe")
    username = me.get("username", "<unknown>")
    print(f"Connected as @{username}")

    if username.lower() != target_username.lower():
        raise RuntimeError(
            f"Connected bot @{username} does not match expected @{target_username}. "
            "Set TG_BOT_USERNAME if you intend to use a different bot."
        )

    offset = 0
    print(f"Listening for messages for @{username}; forwarding each one to {hook_path}")

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

                run_hook(hook_path, update)
        except KeyboardInterrupt:
            print("Stopped.")
            return
        except Exception as exc:
            print(f"Error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
