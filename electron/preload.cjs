const { contextBridge, ipcRenderer } = require("electron");


contextBridge.exposeInMainWorld("telethonProxy", {
  apiGet(path) {
    return ipcRenderer.invoke("proxy:get-json", path);
  },
  apiPost(path, payload) {
    return ipcRenderer.invoke("proxy:post-json", path, payload || {});
  },
  backgroundApiBase() {
    return ipcRenderer.invoke("proxy:api-base");
  },
  runtimeInfo() {
    return ipcRenderer.invoke("proxy:get-runtime");
  },
  copyText(value) {
    return ipcRenderer.invoke("proxy:copy-text", String(value || ""));
  },
  openSystemSettings(kind) {
    return ipcRenderer.invoke("proxy:open-system-settings", String(kind || "files"));
  },
  showItemInFolder(targetPath) {
    return ipcRenderer.invoke("proxy:show-item-in-folder", String(targetPath || ""));
  },
  pickDirectory() {
    return ipcRenderer.invoke("proxy:pick-directory");
  },
  setLaunchOnStart(enabled) {
    return ipcRenderer.invoke("proxy:set-launch-on-start", Boolean(enabled));
  },
  hideWindow() {
    return ipcRenderer.invoke("proxy:hide-window");
  },
});
