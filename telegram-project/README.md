# telegram-project

Small Python project using [Telethon](https://github.com/LonamiWebs/Telethon) to access the Telegram API.

## Setup

1. Create a virtualenv:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your Telegram API credentials.
4. Run the auth check:
   ```bash
   python app.py
   ```
5. List chats and their folders:
   ```bash
   python list_chat_folders.py
   ```
6. Find your old messages in chats not in any custom folder:
   ```bash
   python find_old_unfiled_messages.py
   ```
   This is a dry-run by default. Output is grouped by chat and prints the folder list before the messages.
   Optional flags:
   ```bash
   python find_old_unfiled_messages.py --days 45 --limit-per-chat 10
   python find_old_unfiled_messages.py --delete
   python find_old_unfiled_messages.py --include-direct --delete
   ```
   Notes:
   - dry-run is the default
   - chats that belong to custom folders are printed explicitly as skipped
   - direct 1:1 chats are skipped by default; use `--include-direct` to include them
   - `--delete` revoke-deletes matching messages for everyone where Telegram allows it
   - `--limit-per-chat 0` means no limit and is the default

## Getting Telegram API credentials

Create an app at: https://my.telegram.org/apps

You will need:
- `TG_API_ID`
- `TG_API_HASH`
- a phone number for the Telegram account you want to authorize

## Notes

- Session data is stored in `sessions/sample_account.session`.
- Never commit your real `.env` file or session files.
