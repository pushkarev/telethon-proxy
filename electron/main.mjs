import { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, clipboard, dialog } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { NativeAppBackend } from "./native-backend.mjs";


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");
const UI_ENTRY = path.join(ROOT_DIR, "telegram-project", "webui", "index.html");
const SMOKE_MODE = process.env.TP_SMOKE === "1";
const INTERNAL_API_BASE = "electron://app";
const APP_NAME = "Aardvark";
const APP_ICON_PATH = path.join(ROOT_DIR, "electron", "assets", "aardvark-icon.png");
const APP_TRAY_ICON_PATH = path.join(ROOT_DIR, "electron", "assets", "aardvark-tray.png");

let mainWindow = null;
let isQuitting = false;
let tray = null;
let backendReadyPromise = null;
const backend = new NativeAppBackend();

app.setName(APP_NAME);

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
    title: APP_NAME,
    icon: APP_ICON_PATH,
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
  const trayImage = nativeImage.createFromPath(APP_TRAY_ICON_PATH);
  const fallbackImage = nativeImage.createFromPath(APP_ICON_PATH);
  const image = trayImage.isEmpty() ? fallbackImage : trayImage;
  if (process.platform === "darwin") {
    const resized = image.resize({ width: 18, height: 18 });
    resized.setTemplateImage(true);
    return resized;
  }
  return image.resize({ width: 18, height: 18 });
}

function getLaunchOnStartState() {
  const supported = process.platform === "darwin" || process.platform === "win32";
  if (!supported) {
    return { supported: false, enabled: false };
  }
  try {
    const settings = app.getLoginItemSettings();
    return {
      supported: true,
      enabled: Boolean(settings.openAtLogin),
    };
  } catch {
    return {
      supported: true,
      enabled: false,
    };
  }
}

function setLaunchOnStart(enabled) {
  const desired = Boolean(enabled);
  const current = getLaunchOnStartState();
  if (!current.supported) {
    return current;
  }
  if (process.platform === "darwin") {
    app.setLoginItemSettings({
      openAtLogin: desired,
      openAsHidden: desired,
    });
    return getLaunchOnStartState();
  }
  app.setLoginItemSettings({
    openAtLogin: desired,
  });
  return getLaunchOnStartState();
}

function getRuntimeInfo() {
  return {
    apiBase: INTERNAL_API_BASE,
    appName: APP_NAME,
    backgroundOwner: currentBackgroundOwner(),
    platform: process.platform,
    trayEnabled: !SMOKE_MODE,
    closeBehavior: "hide-to-tray",
    launchOnStart: getLaunchOnStartState(),
  };
}


function updateTrayMenu() {
  if (!tray) {
    return;
  }
  const visible = Boolean(mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible());
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: `${APP_NAME} keeps running in the tray when you close the window.`,
        enabled: false,
      },
      { type: "separator" },
      {
        label: visible ? `Hide ${APP_NAME}` : `Open ${APP_NAME}`,
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
  tray.setToolTip(APP_NAME);
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

async function pickDirectory() {
  const focusedWindow = BrowserWindow.getFocusedWindow() || mainWindow || null;
  const result = await dialog.showOpenDialog(focusedWindow, {
    properties: ["openDirectory"],
  });
  if (result.canceled || !result.filePaths?.length) {
    return { ok: true, canceled: true, path: null };
  }
  return { ok: true, canceled: false, path: result.filePaths[0] };
}


app.on("before-quit", () => {
  isQuitting = true;
  void backend.stop();
});

ipcMain.handle("proxy:api-base", () => INTERNAL_API_BASE);
ipcMain.handle("proxy:get-runtime", () => getRuntimeInfo());
ipcMain.handle("proxy:copy-text", (_event, value) => {
  clipboard.writeText(String(value || ""));
  return { ok: true };
});
ipcMain.handle("proxy:open-system-settings", async (_event, kind) => openSystemSettingsPane(String(kind || "files")));
ipcMain.handle("proxy:show-item-in-folder", async (_event, targetPath) => revealLocalPath(String(targetPath || "")));
ipcMain.handle("proxy:pick-directory", async () => pickDirectory());
ipcMain.handle("proxy:set-launch-on-start", (_event, enabled) => ({
  ok: true,
  launchOnStart: setLaunchOnStart(Boolean(enabled)),
}));
ipcMain.handle("proxy:hide-window", () => {
  hideMainWindow();
  return { ok: true };
});

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
  if (status === 404 && String(routePath).startsWith("/api/filesystem/")) {
    return "Filesystem support needs the internal app worker. Restart the app and try again.";
  }
  return payload?.error || `Request failed with status ${status}`;
}

function apiError(routePath, status, error) {
  const wrapped = new Error(formatApiError(routePath, status, { error: error?.message || String(error) }));
  wrapped.status = status;
  return wrapped;
}

const GET_ROUTE_HANDLERS = new Map([
  ["/api/overview", () => backend.getOverview()],
  ["/api/chat", (url) => backend.getTelegramChat(url.searchParams.get("peer_id") || "0", 50)],
  ["/api/telegram/auth", () => backend.getTelegramAuth()],
  ["/api/whatsapp/auth", () => backend.getWhatsAppAuth()],
  ["/api/whatsapp/chat", (url) => backend.getWhatsAppChat(url.searchParams.get("jid") || "")],
  ["/api/imessage/auth", () => backend.getIMessageAuth()],
  ["/api/imessage/chat", (url) => backend.getIMessageChat(url.searchParams.get("chat_id") || "")],
  ["/api/filesystem/status", () => backend.getFilesystemStatus()],
  ["/api/mcp/token", () => backend.getMcpToken()],
]);

const POST_ROUTE_HANDLERS = new Map([
  ["/api/telegram/auth/save", (payload) => backend.telegramAuth.saveCredentials({
    apiId: payload.api_id,
    apiHash: payload.api_hash,
    phone: payload.phone,
  })],
  ["/api/telegram/auth/request-code", (payload) => backend.telegramAuth.requestCode({ phone: payload.phone })],
  ["/api/telegram/auth/submit-code", (payload) => backend.telegramAuth.submitCode({ code: payload.code })],
  ["/api/telegram/auth/submit-password", (payload) => backend.telegramAuth.submitPassword({ password: payload.password })],
  ["/api/telegram/auth/clear", () => backend.telegramAuth.clearSavedAuth()],
  ["/api/telegram/auth/clear-session", () => backend.telegramAuth.clearSavedSession()],
  ["/api/whatsapp/auth/request-pairing-code", () => backend.whatsapp.requestPairingCode()],
  ["/api/whatsapp/auth/logout", () => backend.whatsapp.logout()],
  ["/api/imessage/visible-chats", (payload) => backend.setIMessageVisibility({ chatId: payload.chat_id, visible: payload.visible })],
  ["/api/imessage/enabled", (payload) => backend.setIMessageEnabled(Boolean(payload.enabled))],
  ["/api/filesystem/directories", (payload) => backend.addFilesystemDirectory(String(payload.path || ""))],
  ["/api/filesystem/directories/toggle", (payload) => backend.setFilesystemDirectoryEnabled(String(payload.path || ""), Boolean(payload.enabled))],
  ["/api/filesystem/directories/remove", (payload) => backend.removeFilesystemDirectory(String(payload.path || ""))],
  ["/api/mcp/token/rotate", () => backend.rotateMcpToken()],
  ["/api/mcp/config", (payload) => backend.setMcpConfig({ host: payload.host, port: payload.port, scheme: payload.scheme })],
]);

async function handleApiGet(routePath) {
  const url = new URL(routePath, `${INTERNAL_API_BASE}/`);
  const handler = GET_ROUTE_HANDLERS.get(url.pathname);
  if (!handler) {
    throw apiError(routePath, 404, new Error("Not Found"));
  }
  await waitForBackendReady();
  try {
    return await handler(url);
  } catch (error) {
    throw apiError(routePath, error?.status || 500, error);
  }
}

async function handleApiPost(routePath, payload = {}) {
  const handler = POST_ROUTE_HANDLERS.get(routePath);
  if (!handler) {
    throw apiError(routePath, 404, new Error("Not Found"));
  }
  await waitForBackendReady();
  try {
    return await handler(payload);
  } catch (error) {
    throw apiError(routePath, error?.status || 500, error);
  }
}

ipcMain.handle("proxy:get-json", async (_event, routePath) => handleApiGet(String(routePath || "")));
ipcMain.handle("proxy:post-json", async (_event, routePath, payload) => handleApiPost(String(routePath || ""), payload || {}));

app.whenReady().then(async () => {
  if (process.platform === "darwin" && !SMOKE_MODE) {
    app.setActivationPolicy("accessory");
    app.dock.setIcon(APP_ICON_PATH);
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
