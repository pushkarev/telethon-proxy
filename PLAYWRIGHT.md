# Playwright setup

This workspace now has a practical local Playwright starter aimed at browser-assisted tasks.

## What it does

- Uses the system Brave browser at `/usr/bin/brave-browser`
- Keeps a persistent profile in `.openclaw/playwright-profile`
- Writes screenshots to `.openclaw/playwright-output`
- Opens a URL and prints a small JSON summary

## Install dependencies

```bash
npm install playwright
```

No separate browser download is required for this starter, because it targets the existing Brave install.

## Usage

Open a page and print summary:

```bash
npm run browser:run -- https://example.com
```

Open a page and save a screenshot:

```bash
npm run browser:shot -- https://example.com
```

Run headed mode for manual login steps:

```bash
PLAYWRIGHT_HEADLESS=false npm run browser:run -- https://example.com
```

Use a different Chrome/Chromium binary:

```bash
PLAYWRIGHT_EXECUTABLE_PATH=/path/to/browser npm run browser:run -- https://example.com
```

## Practical limits

This is browser automation, not magic.

- Captchas may require manual completion.
- Purchases should still require explicit human approval before final submit.
- Some sites block automation or require additional anti-bot handling.

## Good pattern for reservations

1. Open headed mode.
2. Log in manually if needed.
3. Let the script navigate/search/fill fields.
4. Pause before final confirmation.
5. Review and approve the last step manually.
