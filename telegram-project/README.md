# telegram-project

This repo now ships as a single Electron app with an embedded Node backend.

It exposes:

- Telegram chats scoped to the `Cloud` folder
- WhatsApp chats scoped to the `Cloud` label
- local Messages chats that you explicitly mark visible
- an authenticated MCP endpoint for downstream tools

## Setup

1. Install dependencies from the repo root:
   ```bash
   npm install
   ```
2. Create the local config directory and copy the example env:
   ```bash
   mkdir -p ~/.tlt-proxy
   cp telegram-project/.env.example ~/.tlt-proxy/.env
   ```

## Run

From the repo root:

```bash
npm run app:dev
```

The Electron app hosts both the UI and the local backend. There is no separate Python service anymore.

## Desktop app flow

Use the desktop UI to complete setup:

- `Telegram -> Settings` to save `api_id` / `api_hash`, request a login code, and authorize the upstream account
- `WhatsApp -> Settings` to link the local bridge with a QR code
- `Messages -> All chats` to choose which local chats should be visible through MCP
- `MCP` to copy the local bearer token and adjust the bind protocol, interface, and port

## Scope rules

- Telegram only exposes chats inside the special `Cloud` folder.
- WhatsApp only exposes chats carrying the `Cloud` label.
- Messages only exposes chats that you check in `Messages -> All chats`.

## MCP

Default local endpoint:

```text
http://127.0.0.1:8795/mcp
```

The bearer token is managed locally and can be copied or rotated from the app.

## Tests

Run the smoke test from the repo root:

```bash
npm run app:smoke
```

Run the focused Node tests:

```bash
node --test electron/gramjs-background.test.mjs whatsapp-project/service.test.mjs
```

## Notes

- Config defaults live under `~/.tlt-proxy/`
- On macOS, Telegram credentials and session data are stored in Keychain
- Messages history requires access to `~/Library/Messages/chat.db`, which may require Full Disk Access
- HTTPS MCP listeners require `TP_MCP_TLS_CERT` and `TP_MCP_TLS_KEY`
- Never commit your real env file, tokens, or session material
