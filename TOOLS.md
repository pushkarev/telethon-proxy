# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

## Secrets

- Doppler CLI use is pre-approved for internal work in this workspace.
- Do not ask for confirmation before using `doppler` to inspect configs, fetch secrets, or run commands with injected env for local development and debugging.
- Keep the secret values out of repo files and avoid echoing them back unless explicitly asked.

## Approvals

- Safe system calls that are necessary to complete the current task are pre-approved from the assistant side.
- Do not stop to ask in chat before running routine task-critical commands such as local interpreters, package installs in user or project scope, network calls needed for the task, or OS helpers like `open`.
- Still be careful with destructive or high-risk actions, and note that platform-enforced approval prompts may still appear even when user-level chat confirmation is not needed.
- Updated preference: ask for confirmation before running local commands needed to move development forward, even when the commands are otherwise routine and safe.

## Git Workflow

- Prefer one feature per branch for parallel Codex work.
- Prefer one Codex thread per branch/worktree.
- See `THREADS.md` for the current branch-to-worktree map for this project.
