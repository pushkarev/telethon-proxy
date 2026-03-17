# Telegram Proxy v1 Plan

## Goal

Build a **Telegram-compatible constrained proxy** backed by one upstream Telethon session.
Downstream clients should eventually connect using Telethon and only see chats that are in the upstream folder named `Cloud`.

## Current state

This repository now contains the **policy-critical core**:

- config loading
- Cloud folder snapshot builder
- allow/deny policy model
- message/entity filtering
- update fanout bus
- upstream Telethon adapter
- executable server skeleton for integration work
- tests covering the filtering rules

## Deliberate non-goal in this commit

This commit does **not** yet implement the MTProto wire protocol that unmodified Telethon clients require.
That is the next major milestone.

## v1 milestones

### M1: Policy core and integration harness
- [x] Resolve allowed peers from folder named `Cloud`
- [x] Filter history results to allowed chats only
- [x] Allow member listing inside allowed chats
- [x] Block actions targeting peers outside Cloud
- [x] Publish filtered updates from upstream
- [x] Provide a local control server for testing the policy engine

### M2: Request surface for required downstream behavior
- [ ] Dialog list
- [ ] History
- [ ] Send text
- [ ] Read acknowledgements
- [ ] Participant listing
- [ ] Media download/upload policy
- [ ] Search and entity resolution policy

### M3: Downstream virtual session state
- [ ] Maintain proxy-side pts/qts/seq state
- [ ] Filter and synthesize updates consistently
- [ ] Prevent hidden upstream updates from corrupting downstream state

### M4: MTProto compatibility layer
- [ ] Transport framing
- [ ] Auth key negotiation
- [ ] Minimal request dispatcher
- [ ] `invokeWithLayer` / `initConnection`
- [ ] TL object encode/decode surface for selected methods

## Practical next step

Use the current harness to validate the filtering semantics against a real upstream account:

```bash
cd telegram-project
source .venv/bin/activate
python -m pytest tests/test_filtering.py
python proxy_main.py
```

Then we can add the first real downstream-facing method map and start replacing the JSON harness with TL/MTProto handling.
