# THREADS.md - Branch And Thread Layout

Use one Codex thread per git branch and, when possible, one worktree per active feature.

## Active Layout

- `codex/integration-wip`
  Path: `/Users/dmitry/dev/telethon-proxy`
  Purpose: temporary landing zone for already-mixed local work that existed before the branch split.
  Rule: do not start new feature work here unless the task is explicitly about integration, conflict resolution, or branch management.

- `codex/telegram-auth-desktop`
  Path: `/Users/dmitry/dev/telethon-proxy-telegram-auth`
  Purpose: Telegram auth flow, Keychain-backed secrets, Electron shell, desktop UI polish, and related review fixes.

- `codex/whatsapp-mcp`
  Path: `/Users/dmitry/dev/telethon-proxy-whatsapp-mcp`
  Purpose: WhatsApp bridge, WhatsApp dashboard surfaces, WhatsApp MCP tools/resources, and related backend wiring.

## Workflow

- Open the Codex thread that matches the branch you are working on.
- Keep feature work in its feature worktree instead of stacking unrelated changes into `integration-wip`.
- Cherry-pick or merge finished commits back through the integration branch only when you are intentionally reconciling features.
- If a new feature starts, create a new `codex/<feature-name>` branch and a sibling worktree for it before changing code.

## Naming

- Branches: `codex/<feature>`
- Worktrees: `/Users/dmitry/dev/telethon-proxy-<feature>`
- Threads: name them after the branch they are attached to
