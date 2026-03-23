import { app, BrowserWindow, dialog } from "electron";
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
const DASHBOARD_URL = `http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/`;
const PYTHON_BIN = process.env.TP_PYTHON_BIN || "python3";

let mainWindow = null;
let backgroundProcess = null;
let isQuitting = false;
let ownsBackgroundProcess = false;


function backgroundEnv() {
  return {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    TP_DASHBOARD_HOST: DASHBOARD_HOST,
    TP_DASHBOARD_PORT: String(DASHBOARD_PORT),
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
  if (!fs.existsSync(spec.command)) {
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
      const response = await fetch(new URL("/api/overview", DASHBOARD_URL));
      if (response.ok) {
        return;
      }
    } catch {
      // Retry until the service comes up.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Dashboard did not become ready at ${DASHBOARD_URL} within ${timeoutMs}ms`);
}


async function isDashboardReady() {
  try {
    const response = await fetch(new URL("/api/overview", DASHBOARD_URL));
    return response.ok;
  } catch {
    return false;
  }
}


async function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.focus();
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
    },
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  await mainWindow.loadURL(DASHBOARD_URL);
  return mainWindow;
}


app.on("before-quit", () => {
  isQuitting = true;
  if (backgroundProcess && ownsBackgroundProcess) {
    backgroundProcess.kill("SIGTERM");
  }
});

app.whenReady().then(async () => {
  if (!(await isDashboardReady())) {
    startBackgroundService();
  }
  await createWindow();
  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createWindow();
    }
  });
}).catch((error) => {
  console.error(error);
  app.exit(1);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
