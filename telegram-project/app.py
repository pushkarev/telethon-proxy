import argparse
import asyncio
import datetime as dt
import getpass
import io
import os
import subprocess
from pathlib import Path

from config_paths import load_project_env
import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telegram_auth import prompt_value, resolve_runtime_credentials

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authorize the upstream Telegram account.")
    parser.add_argument(
        "--qr",
        action="store_true",
        help="Use Telegram QR login instead of requesting a numeric login code.",
    )
    parser.add_argument(
        "--open-link",
        action="store_true",
        help="On macOS, open the QR login tg:// link with the default handler after printing it.",
    )
    parser.add_argument(
        "--qr-terminal",
        action="store_true",
        help="Render a scannable QR directly in the terminal during QR login.",
    )
    parser.add_argument(
        "--qr-png",
        nargs="?",
        const=str(Path.home() / ".tlt-proxy/telegram-login-qr.png"),
        help="Also save the QR login code as a PNG image. Defaults to ~/.tlt-proxy/telegram-login-qr.png.",
    )
    return parser.parse_args()


def render_terminal_qr(url: str) -> str:
    stream = io.StringIO()
    qr = qrcode.QRCode(border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(out=stream, tty=False, invert=True)
    return stream.getvalue()


def save_qr_png(url: str, destination: str) -> Path:
    path = Path(destination).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(url)
    image.save(path)
    return path


async def authorize_with_qr(
    client: TelegramClient,
    *,
    open_link: bool,
    qr_terminal: bool,
    qr_png: str | None,
) -> None:
    attempt = 1
    while True:
        qr_login = await client.qr_login()
        expires_in = max(
            int((qr_login.expires - dt.datetime.now(tz=dt.timezone.utc)).total_seconds()),
            0,
        )

        print(f"QR login attempt {attempt}")
        print("This tg:// URL is the same payload a QR code would contain.")
        print("Open this link on a device where Telegram is already logged in:")
        print(qr_login.url)
        if qr_terminal:
            print()
            print(render_terminal_qr(qr_login.url))
            print()
        if qr_png:
            png_path = save_qr_png(qr_login.url, qr_png)
            print(f"Saved QR PNG: {png_path}")
        print("If Telegram Desktop on this Mac is logged in, you can run:")
        print(f"open '{qr_login.url}'")
        if open_link:
            subprocess.run(["open", qr_login.url], check=False)
        print(f"Waiting for Telegram QR login approval for up to ~{expires_in} seconds...")

        try:
            await qr_login.wait(timeout=max(expires_in, 5))
            return
        except asyncio.TimeoutError:
            attempt += 1
            print("QR login timed out before approval. Generating a fresh QR...")


async def run_login(use_qr: bool, open_link: bool, qr_terminal: bool, qr_png: str | None) -> None:
    load_project_env()

    credentials = resolve_runtime_credentials(require_phone=not use_qr)
    session_name = os.getenv("TG_SESSION_NAME", str(Path.home() / ".tlt-proxy/sessions/sample_account"))

    session_path = Path(session_name).expanduser()
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), credentials.api_id, credentials.api_hash)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            try:
                if use_qr:
                    await authorize_with_qr(
                        client,
                        open_link=open_link,
                        qr_terminal=qr_terminal,
                        qr_png=qr_png,
                    )
                else:
                    sent = await client.send_code_request(credentials.phone)
                    delivery = type(sent.type).__name__
                    print(f"Telegram requested a login code via: {delivery}")
                    if getattr(sent, "timeout", None):
                        print(f"Retry available after: {sent.timeout} seconds")
                    code = input("Telegram login code: ").strip()
                    await client.sign_in(
                        phone=credentials.phone,
                        code=code,
                        phone_code_hash=sent.phone_code_hash,
                    )
            except SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)

        me = await client.get_me()
        print("Authorized successfully.")
        print(f"User id: {me.id}")
        print(f"Name: {getattr(me, 'first_name', '')} {getattr(me, 'last_name', '')}".strip())
        print(f"Username: @{me.username}" if getattr(me, 'username', None) else "Username: <none>")
    finally:
        await client.disconnect()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run_login(
            use_qr=args.qr,
            open_link=args.open_link,
            qr_terminal=args.qr_terminal,
            qr_png=args.qr_png,
        )
    )


if __name__ == "__main__":
    main()
