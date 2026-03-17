#!/usr/bin/env node
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const args = process.argv.slice(2);
const wantsScreenshot = args.includes('--screenshot');
const url = args.find(arg => !arg.startsWith('--')) || process.env.PLAYWRIGHT_URL || 'https://example.com';
const executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH || '/usr/bin/brave-browser';
const headless = process.env.PLAYWRIGHT_HEADLESS !== 'false';
const userDataDir = process.env.PLAYWRIGHT_USER_DATA_DIR || path.resolve('.openclaw/playwright-profile');
const outDir = path.resolve('.openclaw/playwright-output');

async function main() {
  await fs.mkdir(userDataDir, { recursive: true });
  await fs.mkdir(outDir, { recursive: true });

  const context = await chromium.launchPersistentContext(userDataDir, {
    executablePath,
    headless,
    viewport: { width: 1440, height: 960 },
    args: ['--no-first-run', '--no-default-browser-check']
  });

  try {
    const page = context.pages()[0] || await context.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const title = await page.title();
    const summary = {
      url: page.url(),
      title,
      timestamp: new Date().toISOString()
    };

    if (wantsScreenshot) {
      const shotPath = path.join(outDir, `shot-${Date.now()}.png`);
      await page.screenshot({ path: shotPath, fullPage: true });
      summary.screenshot = shotPath;
    }

    console.log(JSON.stringify(summary, null, 2));
  } finally {
    await context.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
