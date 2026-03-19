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
3. Create the default config directory and copy the example env there:
   ```bash
   mkdir -p ~/.tlt-proxy
   cp .env.example ~/.tlt-proxy/.env
   ```
4. Fill in your Telegram API credentials in `~/.tlt-proxy/.env`.
5. Run the auth check:
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
   - messages containing images/photos are always skipped and never deleted
   - `--delete` revoke-deletes matching messages for everyone where Telegram allows it
   - `--limit-per-chat 0` means no limit and is the default

## Telegram Bot API hook listener

If you want a bot that quietly invokes a local hook for every incoming message to `@fewijhca3fih4bot`, use:

```bash
python reply_ok_bot.py
```

Add these values to `.env`:

```env
TG_BOT_TOKEN=123456:your_bot_token_here
TG_BOT_USERNAME=fewijhca3fih4bot
TG_BOT_HOOK_PATH=/home/ubuntu/incoming_hook.sh
```

Notes:
- This uses the Telegram **Bot API** with long polling (`getUpdates`).
- The token must belong to the bot account you want to run.
- By default it verifies that the authenticated bot username matches `fewijhca3fih4bot`.
- It does not reply in Telegram; it just runs the hook locally.
- The raw Telegram update JSON is passed to the hook on stdin.

## Getting Telegram API credentials

Create an app at: https://my.telegram.org/apps

You will need:
- `TG_API_ID`
- `TG_API_HASH`
- a phone number for the Telegram account you want to authorize

## Notes

- By default, config lives in `~/.tlt-proxy/.env`.
- By default, session data is stored under `~/.tlt-proxy/sessions/`.
- Never commit your real env file or session files.

## Telegram-compatible proxy scaffold

A first v1 scaffold now lives under `telegram_proxy/`.

What exists today:
- Cloud-folder allowlist resolution
- policy enforcement for history/send/read/participants
- filtered update fanout from one upstream Telethon session
- a simple local JSON control server (`proxy_main.py`) for integration testing
- unit tests for the core filtering rules

What does **not** exist yet:
- MTProto wire compatibility for unmodified downstream Telethon clients

Run the current scaffold:

```bash
source .venv/bin/activate
python -m unittest tests.test_filtering -v
python proxy_main.py
```


### Control server request examples

Once `python proxy_main.py` is running, you can talk to the integration harness with newline-delimited JSON:

```bash
printf '{"method":"get_state"}
' | nc 127.0.0.1 9000
printf '{"method":"get_dialogs","limit":20}
' | nc 127.0.0.1 9000
printf '{"method":"get_history","peer":"me","limit":20}
' | nc 127.0.0.1 9000
```

Supported harness methods today:
- `get_state`
- `refresh_policy`
- `get_dialogs`
- `get_history`
- `send_message`
- `mark_read`
- `list_participants`


## Proxy setup for a real Telegram account

1. Copy the proxy env template into the default config location:
   ```bash
   mkdir -p ~/.tlt-proxy
   cp .env.proxy.example ~/.tlt-proxy/.env
   ```
2. Fill in `~/.tlt-proxy/.env`:
   - `TG_API_ID`
   - `TG_API_HASH`
   - `TG_PHONE`
3. Check config:
   ```bash
   source .venv/bin/activate
   python proxy_setup_check.py
   ```
4. Authorize the upstream account:
   ```bash
   python app.py
   ```
5. Verify the folder named `Cloud` exists and contains the chats you want:
   ```bash
   python list_chat_folders.py
   ```
6. Start the proxy harness:
   ```bash
   python proxy_main.py
   ```


### Downstream auth model

Downstream clients should **not** use real Telegram login codes or real Telegram 2FA.
Those are reserved for the single upstream account only.

The proxy now models a separate downstream auth flow:
- `auth_send_code` validates proxy-issued `api_id` / `api_hash`
- the proxy returns a fake `phone_code_hash`
- `auth_sign_in` accepts a proxy-configured fake login code
- optional proxy-side password support is separate from Telegram 2FA

Environment variables:
- `TP_DOWNSTREAM_API_ID`
- `TP_DOWNSTREAM_API_HASH`
- `TP_DOWNSTREAM_LOGIN_CODE`
- `TP_DOWNSTREAM_PASSWORD`


### Messaging-focused proxy surface

The current harness now covers the messaging slice that matters most for an LLM-style Telegram client:
- `resolve_peer`
- `get_dialogs`
- `get_history`
- `get_mentions`
- `send_message`
- `mark_read`
- `list_participants`
- pushed updates with `incoming` / `mentioned` metadata
- optional local incoming hook via `TP_INCOMING_HOOK`

Still out of scope for now:
- account settings
- privacy/config mutation
- folders mutation
- general Telegram feature completeness
