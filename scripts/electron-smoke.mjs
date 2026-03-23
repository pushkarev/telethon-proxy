#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "playwright";
import electronBinary from "electron";


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");

const env = {
  ...process.env,
  TP_SMOKE: "1",
  TP_CONTROL_PORT: process.env.TP_CONTROL_PORT || "9900",
  TP_MTPROTO_PORT: process.env.TP_MTPROTO_PORT || "9901",
  TP_DASHBOARD_PORT: process.env.TP_DASHBOARD_PORT || "9788",
  TP_MCP_PORT: process.env.TP_MCP_PORT || "9791",
};

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForFirstWindow(electronApp, stdio, timeoutMs = 40000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const existingWindows = electronApp.windows();
      if (existingWindows.length > 0) {
        return existingWindows[0];
      }
    } catch (error) {
      throw new Error(`Electron app closed before exposing a window.\n${stdio.join("")}\n${error}`);
    }
    await delay(250);
  }
  throw new Error(`Electron app did not create a window.\n${stdio.join("")}`);
}

async function main() {
  const electronApp = await electron.launch({
    executablePath: electronBinary,
    args: [path.join(ROOT_DIR, "electron", "main.mjs")],
    cwd: ROOT_DIR,
    env,
  });

  const pageErrors = [];
  const consoleErrors = [];
  const stdio = [];
  electronApp.process()?.stdout?.on("data", (chunk) => stdio.push(String(chunk)));
  electronApp.process()?.stderr?.on("data", (chunk) => stdio.push(String(chunk)));

  try {
    const window = await waitForFirstWindow(electronApp, stdio);
    window.on("pageerror", (error) => {
      pageErrors.push(String(error));
    });
    window.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push(message.text());
      }
    });

    await window.waitForLoadState("domcontentloaded");
    await window.getByRole("button", { name: "Telegram" }).click();
    await window.getByRole("button", { name: "Settings" }).click();
    await window.getByText("Telegram app credentials").waitFor({ timeout: 10000 });
    await window.getByRole("button", { name: "Request login code" }).waitFor({ timeout: 10000 });

    const summary = {
      title: await window.title(),
      url: window.url(),
      pageErrors,
      consoleErrors,
      hasTelegramSettings: await window.getByText("Telegram app credentials").isVisible(),
      hasRequestCodeButton: await window.getByRole("button", { name: "Request login code" }).isVisible(),
    };

    console.log(JSON.stringify(summary, null, 2));

    if (pageErrors.length || consoleErrors.length) {
      process.exitCode = 1;
    }
  } finally {
    await electronApp.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
