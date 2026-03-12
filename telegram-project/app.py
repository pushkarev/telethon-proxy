import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    load_dotenv()

    api_id = int(require_env("TG_API_ID"))
    api_hash = require_env("TG_API_HASH")
    phone = require_env("TG_PHONE")
    session_name = os.getenv("TG_SESSION_NAME", "sessions/sample_account")

    session_path = Path(session_name)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), api_id, api_hash)

    with client:
        client.start(phone=phone)
        me = client.get_me()
        print("Authorized successfully.")
        print(f"User id: {me.id}")
        print(f"Name: {getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip())
        print(f"Username: @{me.username}" if getattr(me, 'username', None) else "Username: <none>")


if __name__ == "__main__":
    main()
