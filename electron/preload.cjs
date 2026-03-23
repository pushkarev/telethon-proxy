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
  copyText(value) {
    return ipcRenderer.invoke("proxy:copy-text", String(value || ""));
  },
});
