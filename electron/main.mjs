import { app, BrowserWindow, Menu, Tray, dialog, nativeImage, ipcMain, shell, clipboard } from "electron";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");
const TELEGRAM_DIR = path.join(ROOT_DIR, "telegram-project");
const DASHBOARD_HOST = process.env.TP_DASHBOARD_HOST || "127.0.0.1";
const DASHBOARD_PORT = Number(process.env.TP_DASHBOARD_PORT || "8788");
const BACKGROUND_API_BASE = `http://${DASHBOARD_HOST}:${DASHBOARD_PORT}`;
const PYTHON_BIN = process.env.TP_PYTHON_BIN || "python3";
const UI_ENTRY = path.join(ROOT_DIR, "telegram-project", "webui", "index.html");
const SMOKE_MODE = process.env.TP_SMOKE === "1";
const REQUIRED_API_PATHS = ["/api/overview", "/api/telegram/auth", "/api/mcp/token", "/api/whatsapp/auth"];

let mainWindow = null;
let backgroundProcess = null;
let isQuitting = false;
let ownsBackgroundProcess = false;
let tray = null;


function backgroundEnv() {
  return {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    TP_DASHBOARD_HOST: DASHBOARD_HOST,
    TP_DASHBOARD_PORT: String(DASHBOARD_PORT),
    ...(app.isPackaged ? { TP_NODE_BIN: process.execPath } : {}),
  };
}


function backgroundSpec() {
  if (app.isPackaged) {
    const executableName = process.platform === "win32" ? "telethon-proxy-service.exe" : "telethon-proxy-service";
    const executable = path.join(process.resourcesPath, "background", "telethon-proxy-service", executableName);
    return {
      command: executable,
      args: [],
      cwd: path.dirname(executable),
    };
  }
  return {
    command: PYTHON_BIN,
    args: ["proxy_service.py"],
    cwd: TELEGRAM_DIR,
  };
}


function startBackgroundService() {
  if (backgroundProcess) {
    return backgroundProcess;
  }
  const spec = backgroundSpec();
  const commandLooksLikePath = spec.command.includes(path.sep) || spec.command.startsWith(".");
  if (commandLooksLikePath && !fs.existsSync(spec.command)) {
    throw new Error(`Background service executable not found: ${spec.command}`);
  }
  ownsBackgroundProcess = true;
  backgroundProcess = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: backgroundEnv(),
    stdio: ["ignore", "pipe", "pipe"],
  });
  backgroundProcess.stdout.on("data", (chunk) => {
    process.stdout.write(`[proxy] ${chunk}`);
  });
  backgroundProcess.stderr.on("data", (chunk) => {
    process.stderr.write(`[proxy] ${chunk}`);
  });
  backgroundProcess.on("exit", async (code, signal) => {
    backgroundProcess = null;
    if (isQuitting) {
      return;
    }
    const detail = `Background proxy exited (${signal ? `signal ${signal}` : `code ${code}`}).`;
    if (mainWindow && !mainWindow.isDestroyed()) {
      await dialog.showMessageBox(mainWindow, {
        type: "error",
        message: "Telethon Proxy background service stopped",
        detail,
      });
    }
  });
  return backgroundProcess;
}


async function waitForDashboard(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (await isDashboardReady()) {
        return;
      }
    } catch {
      // Retry until the service comes up.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Background API did not become ready at ${BACKGROUND_API_BASE} within ${timeoutMs}ms`);
}


async function isDashboardReady() {
  for (const routePath of REQUIRED_API_PATHS) {
    try {
      const response = await fetch(new URL(routePath, `${BACKGROUND_API_BASE}/`));
      if (!response.ok) {
        return false;
      }
    } catch {
      return false;
    }
  }
  return true;
}


async function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    showMainWindow();
    return mainWindow;
  }
  await waitForDashboard();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 980,
    minWidth: 1100,
    minHeight: 760,
    autoHideMenuBar: true,
    backgroundColor: "#f6f0e4",
    title: "Telethon Proxy",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.cjs"),
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url).catch(() => {});
    return { action: "deny" };
  });
  mainWindow.on("close", (event) => {
    if (isQuitting) {
      return;
    }
    event.preventDefault();
    hideMainWindow();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  await mainWindow.loadFile(UI_ENTRY);
  showMainWindow();
  return mainWindow;
}


function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  if (process.platform === "darwin") {
    if (SMOKE_MODE) {
      return;
    }
    app.dock.show();
  }
  mainWindow.show();
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.focus();
  updateTrayMenu();
}


function hideMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  mainWindow.hide();
  if (process.platform === "darwin") {
    if (SMOKE_MODE) {
      return;
    }
    app.dock.hide();
  }
  updateTrayMenu();
}


function createTrayImage() {
  if (process.platform === "darwin") {
    return nativeImage.createFromDataURL("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s1vZ1cAAAAASUVORK5CYII=");
  }
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
      <g fill="none" fill-rule="evenodd">
        <path d="M5.5 7h11" stroke="#000000" stroke-width="1.8" stroke-linecap="round"/>
        <path d="M5.5 11h11" stroke="#000000" stroke-width="1.8" stroke-linecap="round"/>
        <path d="M5.5 15h7" stroke="#000000" stroke-width="1.8" stroke-linecap="round"/>
      </g>
    </svg>
  `;
  const image = nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`);
  return image.resize({ width: 18, height: 18 });
}


function updateTrayMenu() {
  if (!tray) {
    return;
  }
  const visible = Boolean(mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible());
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: visible ? "Hide Telethon Proxy" : "Open Telethon Proxy",
        click: async () => {
          if (!mainWindow || mainWindow.isDestroyed()) {
            await createWindow();
            return;
          }
          if (mainWindow.isVisible()) {
            hideMainWindow();
          } else {
            showMainWindow();
          }
          updateTrayMenu();
        },
      },
      {
        label: "Quit",
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ]),
  );
}


function createTray() {
  if (tray) {
    return tray;
  }
  if (SMOKE_MODE) {
    return null;
  }
  tray = new Tray(createTrayImage());
  tray.setToolTip("Telethon Proxy");
  if (process.platform === "darwin") {
    tray.setTitle("TP");
  }
  tray.on("click", async () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      await createWindow();
      updateTrayMenu();
      return;
    }
    if (mainWindow.isVisible()) {
      hideMainWindow();
    } else {
      showMainWindow();
    }
    updateTrayMenu();
  });
  updateTrayMenu();
  return tray;
}


app.on("before-quit", () => {
  isQuitting = true;
  if (backgroundProcess && ownsBackgroundProcess) {
    backgroundProcess.kill("SIGTERM");
  }
});

ipcMain.handle("proxy:api-base", () => BACKGROUND_API_BASE);
ipcMain.handle("proxy:copy-text", (_event, value) => {
  clipboard.writeText(String(value || ""));
  return { ok: true };
});

async function readJsonOrText(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { error: text || `Request failed with status ${response.status}` };
  }
}

function formatApiError(routePath, status, payload) {
  if (status === 404 && String(routePath).startsWith("/api/telegram/auth")) {
    return "Telegram Settings needs a newer background service. Restart the app or stop the older local proxy service and try again.";
  }
  if (status === 404 && String(routePath).startsWith("/api/whatsapp/auth")) {
    return "WhatsApp support needs a newer background service. Restart the app or stop the older local proxy service and try again.";
  }
  return payload.error || `Request failed with status ${status}`;
}

ipcMain.handle("proxy:get-json", async (_event, routePath) => {
  const response = await fetch(new URL(routePath, `${BACKGROUND_API_BASE}/`));
  const payload = await readJsonOrText(response);
  if (!response.ok) {
    throw new Error(formatApiError(routePath, response.status, payload));
  }
  return payload;
});
ipcMain.handle("proxy:post-json", async (_event, routePath, payload) => {
  const response = await fetch(new URL(routePath, `${BACKGROUND_API_BASE}/`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await readJsonOrText(response);
  if (!response.ok) {
    throw new Error(formatApiError(routePath, response.status, data));
  }
  return data;
});

app.whenReady().then(async () => {
  if (process.platform === "darwin" && !SMOKE_MODE) {
    app.setActivationPolicy("accessory");
  }
  createTray();
  if (!(await isDashboardReady())) {
    startBackgroundService();
  }
  await createWindow();
  updateTrayMenu();
  app.on("activate", async () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      await createWindow();
    } else {
      showMainWindow();
    }
    updateTrayMenu();
  });
}).catch((error) => {
  console.error(error);
  app.exit(1);
});

app.on("window-all-closed", () => {
  updateTrayMenu();
});
