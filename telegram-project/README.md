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
- a local MTProto endpoint for Telethon clients via `proxy_service.py`
- downstream proxy-session issuing so clients do not receive upstream Telegram secrets
- unit and integration tests for both filtering rules and the Telethon-facing endpoint

Important compatibility note:
- downstream Telethon clients need a **proxy-issued session string**
- the proxy does **not** implement Telegram's public-RSA first-connect handshake for arbitrary new clients
- this keeps the design local and prevents exposing upstream Telegram auth material

Run the test suite:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
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
   Or, if you prefer not to keep Telegram app credentials in a file, leave them unset and enter them interactively when running `app.py --qr`.
3. Check config:
   ```bash
   source .venv/bin/activate
   python proxy_setup_check.py
   ```
4. Authorize the upstream account:
   ```bash
   python app.py
   ```
   Or run it with inline env vars for a one-off interactive login:
   ```bash
   TG_API_ID=12345 \
   TG_API_HASH=your_api_hash \
   TG_PHONE=+15550000000 \
   TG_SESSION_NAME=~/.tlt-proxy/sessions/proxy_upstream \
   python app.py
   ```
   The script will prompt in the terminal for the Telegram login code and, if needed, the 2FA password.
   For QR login without storing Telegram app credentials in `~/.tlt-proxy/.env`:
   ```bash
   TG_SESSION_NAME=~/.tlt-proxy/sessions/proxy_upstream \
   python app.py --qr --qr-png
   ```
   That flow prompts for `TG_API_ID` and `TG_API_HASH` interactively, saves a QR PNG, and does not require `TG_PHONE`.
   If the QR expires before you approve it, the tool now regenerates a fresh QR instead of exiting.
5. Verify the folder named `Cloud` exists and contains the chats you want:
   ```bash
   python list_chat_folders.py
   ```
6. Start the proxy service:
   ```bash
   python proxy_service.py
   ```
7. Issue a downstream Telethon session:
   ```bash
   python proxy_service.py --issue-session
   ```

Example output:

```text
label=proxy
key_id=123456789
session=1...
api_id=900000
api_hash=dev-proxy-change-me
```

Use those `api_id` / `api_hash` values with the issued `session` in your downstream Telethon client.
The proxy now also prints the advertised `host` / `port` so you can point a client on another machine at the right endpoint.

Example client:

```python
from telethon import TelegramClient
from telethon.sessions import StringSession

client = TelegramClient(StringSession("PASTE_PROXY_SESSION_HERE"), 900000, "dev-proxy-change-me", receive_updates=False)
await client.start(phone="+15550000000", code_callback=lambda: "00000")
dialogs = await client.get_dialogs()
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

The current local endpoint now covers the messaging slice that matters most for an LLM-style Telegram client:
- `resolve_peer`
- `get_dialogs`
- `get_history`
- `send_message`
- `mark_read`
- `list_participants`
- proxy-side `auth.sendCode` / `auth.signIn`
- `help.getConfig`, `users.getUsers(self)`, `updates.getState`, `updates.getDifference`
- optional local incoming hook via `TP_INCOMING_HOOK`

Still out of scope for now:
- arbitrary first-connect Telegram RSA handshake for fresh clients
- account settings
- privacy/config mutation
- folders mutation
- general Telegram feature completeness

### macOS launchd

For local-only testing on the same Mac, the defaults are fine.

For a client on another device in your LAN, add these to `~/.tlt-proxy/.env` first:

```env
TP_MTPROTO_HOST=0.0.0.0
TP_DOWNSTREAM_HOST=YOUR_MAC_LAN_IP
```

Example:

```env
TP_MTPROTO_HOST=0.0.0.0
TP_DOWNSTREAM_HOST=10.11.15.81
```

For Tailscale-only access, keep the bind host at `0.0.0.0` and advertise your Tailscale IPv4 instead:

```env
TP_MTPROTO_HOST=0.0.0.0
TP_DOWNSTREAM_HOST=100.x.y.z
```

Install and start the `launchd` service in one step:

```bash
python proxy_service.py --install-launchd
```

Check status:

```bash
python proxy_service.py --launchd-status
```

Remove it:

```bash
python proxy_service.py --uninstall-launchd
```

If you want the raw plist without installing it, you can still print it:

```bash
python proxy_service.py --print-launchd-plist > ~/Library/LaunchAgents/dev.telethon-proxy.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.telethon-proxy.plist
launchctl enable gui/$(id -u)/dev.telethon-proxy
launchctl kickstart -k gui/$(id -u)/dev.telethon-proxy
```
