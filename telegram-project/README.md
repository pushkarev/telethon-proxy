# telegram-project

Local messaging bridge for:

- Telegram chats scoped to the `Cloud` folder
- WhatsApp chats scoped to the `Cloud` label
- Local Messages chats that you explicitly mark visible
- a local MCP endpoint that exposes those tools and resources

## Setup

1. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create the default config directory and copy the example env:
   ```bash
   mkdir -p ~/.tlt-proxy
   cp .env.example ~/.tlt-proxy/.env
   ```

## Run

Start the local service:

```bash
python proxy_service.py
```

Or install it as a `launchd` agent:

```bash
python proxy_service.py --install-launchd
python proxy_service.py --launchd-status
python proxy_service.py --uninstall-launchd
```

## Desktop app flow

Use the desktop UI to complete setup:

- `Telegram -> Settings` to save `api_id` / `api_hash`, request a login code, and authorize the upstream account
- `WhatsApp -> Settings` to link the local bridge with a QR code
- `Messages -> All chats` to choose which local chats should be visible through MCP
- `MCP` to copy the local bearer token and adjust the bind interface and port

## Scope rules

- Telegram only exposes chats inside the special `Cloud` folder.
- WhatsApp only exposes chats carrying the `Cloud` label.
- Messages only exposes chats that you check in `Messages -> All chats`.

## MCP

Default local endpoint:

```text
http://127.0.0.1:8791/mcp
```

The bearer token is managed locally and can be copied or rotated from the app.

Example helper:

```bash
python list_mcp_chats.py
```

## Utility scripts

- `list_chat_folders.py` lists Telegram folders and chats
- `find_old_unfiled_messages.py` finds old Telegram messages in chats outside custom folders
- `reply_ok_bot.py` runs a simple Bot API hook listener

## Tests

Run the Python tests:

```bash
python -m unittest discover -s tests -v
```

Run the desktop smoke test from the repo root:

```bash
npm run app:smoke
```

## Notes

- Config defaults live under `~/.tlt-proxy/`
- On macOS, Telegram credentials and session data can be stored in Keychain
- Messages history requires access to `~/Library/Messages/chat.db`, which may require Full Disk Access
- Never commit your real env file, tokens, or session material
