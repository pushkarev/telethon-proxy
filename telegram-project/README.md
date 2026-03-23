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
4. Start the app or local service and authorize through `Telegram -> Settings`.
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
- the phone number for the Telegram account you want to authorize in the UI flow

## Notes

- By default, config lives in `~/.tlt-proxy/.env`.
- The supported upstream login flow is now the desktop UI under `Telegram -> Settings`.
- On macOS, that flow stores upstream API credentials and the authorized session in Keychain.
- By default, session data is stored under `~/.tlt-proxy/sessions/` when Keychain is not being used.
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

If you ever spot a detached `python -m unittest discover -s tests -v` process still burning CPU after a tool/session interruption, kill that stray test runner and leave the `launchd`-managed proxy service alone:

```bash
ps -axo pid,ppid,%cpu,%mem,etime,stat,command | rg 'python|proxy_service|unittest'
kill <stray_unittest_pid>
```

The expected long-lived process for this project is `proxy_service.py`, typically loaded as the `dev.telethon-proxy` `launchd` agent.


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
   You can also leave them unset and use the desktop Telegram Settings flow instead, which saves credentials and the upstream session into macOS Keychain.
3. Check config:
   ```bash
   source .venv/bin/activate
   python proxy_setup_check.py
   ```
4. Authorize the upstream account in the UI:
   - start the app or run `python proxy_service.py`
   - open `Telegram -> Settings`
   - open `https://my.telegram.org/auth?to=apps`
   - copy `api_id` and `api_hash`
   - save them
   - request the Telegram login code and complete 2FA if prompted
   - use `Clear Keychain` in the same panel if you want to wipe the saved Telegram credentials and session
5. Verify the folder named `Cloud` exists and contains the chats you want:
   ```bash
   python list_chat_folders.py
   ```
   Only chats placed in that special `Cloud` folder are exposed to downstream proxy clients.
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

### MCP client example

Once `python proxy_service.py` is running, you can list the chats exposed through the local MCP endpoint with:

```bash
python list_mcp_chats.py
```

The script loads `~/.tlt-proxy/.env`, uses the local MCP defaults (`127.0.0.1:8791/mcp`), reads the bearer token from `TP_MCP_TOKEN` or the local macOS Keychain entry, initializes an MCP session, and calls `telegram.list_chats`.

Useful flags:

```bash
python list_mcp_chats.py --json
python list_mcp_chats.py --limit 20
python list_mcp_chats.py --host 127.0.0.1 --port 8791 --path /mcp
```

To monitor all Cloud chats through MCP and echo every new incoming message back into the same chat:

```bash
python echo_mcp_chats.py
```

Useful flags:

```bash
python echo_mcp_chats.py --poll-interval 0.5
python echo_mcp_chats.py --replay-existing
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

For laptop-style intermittent connectivity, you can also tune the upstream reconnect backoff:

```env
TP_UPSTREAM_RECONNECT_MIN_DELAY=2
TP_UPSTREAM_RECONNECT_MAX_DELAY=30
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

### Electron desktop shell

The repo now includes an Electron app entrypoint that opens the desktop UI from local app assets and treats the Python proxy service as the background component.

From the repo root:

```bash
npm install
npm run app:dev
```

Behavior:

- if the local background API is already running on `http://127.0.0.1:8788`, Electron reuses it
- otherwise Electron starts `telegram-project/proxy_service.py` in the background and waits for the API to come up
- the desktop renderer lives in `telegram-project/webui/` and is loaded directly by Electron, while the Python service only serves JSON API routes
- the dashboard now groups Telegram features under a single `Telegram` section with `Chats`, `APIs`, and `Settings`
- the `Telegram -> Settings` panel walks through `my.telegram.org` app key setup, login code entry, and 2FA, stores the upstream keys/session in macOS Keychain, and can clear them again
- closing the app window hides it to the background instead of quitting
- use the system tray/menu bar icon to reopen the window or quit the app completely

To build a distributable macOS app bundle:

```bash
python3 -m venv .venv-build
.venv-build/bin/pip install -r telegram-project/requirements.txt pyinstaller
npm install
npm run app:dist
```

Outputs land in `dist/electron/`, including:

- `Telethon Proxy.app` inside `dist/electron/mac-arm64/`
- `Telethon Proxy-<version>-arm64.dmg`
- `Telethon Proxy-<version>-arm64-mac.zip`
