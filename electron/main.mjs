import { app, BrowserWindow, Menu, Tray, dialog, nativeImage, ipcMain, shell, clipboard } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { NativeAppBackend } from "./native-backend.mjs";


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");
const DASHBOARD_HOST = process.env.TP_DASHBOARD_HOST || "127.0.0.1";
const DASHBOARD_PORT = Number(process.env.TP_DASHBOARD_PORT || "8788");
const UI_ENTRY = path.join(ROOT_DIR, "telegram-project", "webui", "index.html");
const SMOKE_MODE = process.env.TP_SMOKE === "1";
const INTERNAL_API_BASE = "electron://app";

let mainWindow = null;
let isQuitting = false;
let tray = null;
let backendReadyPromise = null;
const backend = new NativeAppBackend();

async function waitForBackendReady() {
  if (!backendReadyPromise) {
    backendReadyPromise = (async () => {
      await backend.start();
    })();
  }
  return backendReadyPromise;
}


async function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    showMainWindow();
    return mainWindow;
  }
  await waitForBackendReady();
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 960,
    minHeight: 680,
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

function currentBackgroundOwner() {
  return "app";
}

async function openSystemSettingsPane(kind = "files") {
  if (process.platform !== "darwin") {
    return { ok: false, opened: false, error: "System settings shortcuts are only available on macOS." };
  }
  const anchor = kind === "automation" ? "Privacy_Automation" : "Privacy_AllFiles";
  const target = `x-apple.systempreferences:com.apple.preference.security?${anchor}`;
  await shell.openExternal(target);
  return { ok: true, opened: true };
}

async function revealLocalPath(targetPath) {
  const resolved = path.resolve(String(targetPath || ""));
  if (!resolved) {
    return { ok: false, revealed: false, error: "A path is required." };
  }
  shell.showItemInFolder(resolved);
  return { ok: true, revealed: true, path: resolved };
}


app.on("before-quit", () => {
  isQuitting = true;
  void backend.stop();
});

ipcMain.handle("proxy:api-base", () => INTERNAL_API_BASE);
ipcMain.handle("proxy:get-runtime", () => ({
  apiBase: INTERNAL_API_BASE,
  backgroundOwner: currentBackgroundOwner(),
  platform: process.platform,
}));
ipcMain.handle("proxy:copy-text", (_event, value) => {
  clipboard.writeText(String(value || ""));
  return { ok: true };
});
ipcMain.handle("proxy:open-system-settings", async (_event, kind) => openSystemSettingsPane(String(kind || "files")));
ipcMain.handle("proxy:show-item-in-folder", async (_event, targetPath) => revealLocalPath(String(targetPath || "")));

function formatApiError(routePath, status, payload = {}) {
  if (status === 404 && String(routePath).startsWith("/api/telegram/auth")) {
    return "Telegram Settings needs the internal app worker. Restart the app and try again.";
  }
  if (status === 404 && String(routePath).startsWith("/api/whatsapp/auth")) {
    return "WhatsApp support needs the internal app worker. Restart the app and try again.";
  }
  if (status === 404 && String(routePath).startsWith("/api/imessage/auth")) {
    return "Messages support needs the internal app worker. Restart the app and try again.";
  }
  return payload?.error || `Request failed with status ${status}`;
}

function apiError(routePath, status, error) {
  const wrapped = new Error(formatApiError(routePath, status, { error: error?.message || String(error) }));
  wrapped.status = status;
  return wrapped;
}

async function backendRequest(method, params = {}, routePath = method) {
  await waitForBackendReady();
  try {
    return await backend[method](...(Array.isArray(params) ? params : [params]));
  } catch (error) {
    throw apiError(routePath, error?.status || 500, error);
  }
}

async function getOverviewPayload() {
  return backend.getOverview();
}

async function getTelegramChat(peerId) {
  return backend.getTelegramChat(peerId, 50);
}

async function handleApiGet(routePath) {
  const url = new URL(routePath, `${INTERNAL_API_BASE}/`);
  if (url.pathname === "/api/overview") {
    return getOverviewPayload();
  }
  if (url.pathname === "/api/chat") {
    return getTelegramChat(url.searchParams.get("peer_id") || "0");
  }
  if (url.pathname === "/api/telegram/auth") {
    return backend.getTelegramAuth();
  }
  if (url.pathname === "/api/whatsapp/auth") {
    return backend.getWhatsAppAuth();
  }
  if (url.pathname === "/api/whatsapp/chat") {
    return backend.getWhatsAppChat(url.searchParams.get("jid") || "");
  }
  if (url.pathname === "/api/imessage/auth") {
    return backend.getIMessageAuth();
  }
  if (url.pathname === "/api/imessage/chat") {
    return backend.getIMessageChat(url.searchParams.get("chat_id") || "");
  }
  if (url.pathname === "/api/mcp/token") {
    return backend.getMcpToken();
  }
  throw apiError(routePath, 404, new Error("Not Found"));
}

async function handleApiPost(routePath, payload = {}) {
  switch (routePath) {
    case "/api/telegram/auth/save":
      return backend.telegramAuth.saveCredentials({
        apiId: payload.api_id,
        apiHash: payload.api_hash,
        phone: payload.phone,
      });
    case "/api/telegram/auth/request-code":
      return backend.telegramAuth.requestCode({ phone: payload.phone });
    case "/api/telegram/auth/submit-code":
      return backend.telegramAuth.submitCode({ code: payload.code });
    case "/api/telegram/auth/submit-password":
      return backend.telegramAuth.submitPassword({ password: payload.password });
    case "/api/telegram/auth/clear":
      return backend.telegramAuth.clearSavedAuth();
    case "/api/telegram/auth/clear-session":
      return backend.telegramAuth.clearSavedSession();
    case "/api/whatsapp/auth/request-pairing-code":
      return backend.whatsapp.authStatus();
    case "/api/whatsapp/auth/logout":
      return backend.whatsapp.logout();
    case "/api/imessage/visible-chats":
      return backend.setIMessageVisibility({ chatId: payload.chat_id, visible: payload.visible });
    case "/api/imessage/enabled":
      return backend.setIMessageEnabled(Boolean(payload.enabled));
    case "/api/mcp/token/rotate":
      return backend.rotateMcpToken();
    case "/api/mcp/config":
      return backend.setMcpConfig({ host: payload.host, port: payload.port, scheme: payload.scheme });
    default:
      throw apiError(routePath, 404, new Error("Not Found"));
  }
}

ipcMain.handle("proxy:get-json", async (_event, routePath) => handleApiGet(String(routePath || "")));
ipcMain.handle("proxy:post-json", async (_event, routePath, payload) => handleApiPost(String(routePath || ""), payload || {}));

app.whenReady().then(async () => {
  if (process.platform === "darwin" && !SMOKE_MODE) {
    app.setActivationPolicy("accessory");
  }
  createTray();
  await waitForBackendReady();
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
