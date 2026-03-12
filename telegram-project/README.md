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
   Optional flags:
   ```bash
   python find_old_unfiled_messages.py --days 45 --limit-per-chat 10
   ```

## Getting Telegram API credentials

Create an app at: https://my.telegram.org/apps

You will need:
- `TG_API_ID`
- `TG_API_HASH`
- a phone number for the Telegram account you want to authorize

## Notes

- Session data is stored in `sessions/sample_account.session`.
- Never commit your real `.env` file or session files.
