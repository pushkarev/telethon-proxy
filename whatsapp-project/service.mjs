import http from "node:http";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import QRCode from "qrcode";
import makeWASocket, {
  ALL_WA_PATCH_NAMES,
  Browsers,
  decodePatches,
  decodeSyncdSnapshot,
  DisconnectReason,
  extractSyncdPatches,
  fetchLatestBaileysVersion,
  newLTHashState,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";

const HOST = process.env.TP_WHATSAPP_HOST || "127.0.0.1";
const PORT = Number.parseInt(process.env.TP_WHATSAPP_PORT || "8792", 10);
const CLOUD_LABEL_NAME = (process.env.TP_WHATSAPP_CLOUD_LABEL || "Cloud").trim() || "Cloud";
const AUTH_DIR = path.resolve(
  process.env.TP_WHATSAPP_AUTH_DIR || path.join(process.env.HOME || process.cwd(), ".tlt-proxy", "whatsapp-auth"),
);
const LABEL_STATE_NAME = "label-state.json";
const BRIDGE_STATE_NAME = "bridge-state.json";
const MAX_CHAT_MESSAGES = 200;
const MAX_UPDATES = 500;
const RECONNECT_DELAY_MS = 2_000;
const MAX_RECONNECT_DELAY_MS = 30_000;
const CONNECT_TIMEOUT_MS = 25_000;
const HISTORY_FETCH_TIMEOUT_MS = 7_000;
const STATE_FLUSH_DELAY_MS = 250;
const HISTORY_ANCHOR_COLLECTIONS = ["regular", "regular_high", "regular_low"];
function nowIso() {
  return new Date().toISOString();
}

function timestampToIso(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number") {
    return new Date(value * 1000).toISOString();
  }
  if (typeof value === "object" && typeof value.toNumber === "function") {
    return new Date(value.toNumber() * 1000).toISOString();
  }
  const numeric = Number(value);
  if (!Number.isNaN(numeric) && Number.isFinite(numeric)) {
    return new Date(numeric * 1000).toISOString();
  }
  return null;
}

function timestampToMs(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number") {
    return value > 1e12 ? value : value * 1000;
  }
  if (typeof value === "object" && typeof value.toNumber === "function") {
    const numeric = value.toNumber();
    return numeric > 1e12 ? numeric : numeric * 1000;
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return null;
  }
  return numeric > 1e12 ? numeric : numeric * 1000;
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function jidKind(jid) {
  if (!jid) {
    return "chat";
  }
  if (jid.endsWith("@g.us")) {
    return "group";
  }
  if (jid.endsWith("@broadcast")) {
    return "broadcast";
  }
  return "dm";
}

function firstDefined(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== "") {
      return value;
    }
  }
  return null;
}

function jidUser(jid) {
  if (!jid) {
    return null;
  }
  const [local] = String(jid).split("@", 1);
  if (!local) {
    return null;
  }
  const [user] = local.split(":", 1);
  return user || null;
}

function displayTitleFromJid(jid) {
  const user = jidUser(jid);
  return user || jid || null;
}

export function peerJidsFromLidMappings(mappingEntries, { meId = null, meLid = null } = {}) {
  const mePnUser = jidUser(meId);
  const meLidUser = jidUser(meLid);
  const peerJids = new Set();
  for (const [pnUser, lidUser] of mappingEntries || []) {
    if (!pnUser || String(pnUser).endsWith("_reverse")) {
      continue;
    }
    if (String(pnUser) === String(mePnUser)) {
      continue;
    }
    if (lidUser !== undefined && lidUser !== null && String(lidUser) === String(meLidUser)) {
      continue;
    }
    peerJids.add(`${pnUser}@s.whatsapp.net`);
  }
  return [...peerJids].sort();
}

function extractText(message) {
  const body = message?.message;
  if (!body) {
    return null;
  }
  return firstDefined(
    body.conversation,
    body.extendedTextMessage?.text,
    body.imageMessage?.caption,
    body.videoMessage?.caption,
    body.documentMessage?.caption,
    body.buttonsResponseMessage?.selectedDisplayText,
    body.listResponseMessage?.title,
    body.templateButtonReplyMessage?.selectedDisplayText,
    body.pollCreationMessage?.name,
    body.reactionMessage?.text,
    body.liveLocationMessage?.caption,
    body.contactMessage?.displayName,
  );
}

function messageKind(message) {
  const body = message?.message;
  if (!body) {
    return "unknown";
  }
  return Object.keys(body)[0] || "unknown";
}

export class WhatsAppBridgeService {
  constructor({ authDir = AUTH_DIR, listen = true } = {}) {
    this.authDir = authDir;
    this.listen = listen;
    this.server = null;
    this.sock = null;
    this.saveCreds = null;
    this.stopping = false;
    this.reconnectTimer = null;
    this.connection = "idle";
    this.connected = false;
    this.registered = false;
    this.qrRaw = null;
    this.qrAscii = null;
    this.qrSvg = null;
    this.lastError = null;
    this.me = null;
    this.startedAt = nowIso();
    this.contacts = new Map();
    this.chats = new Map();
    this.messages = new Map();
    this.labels = new Map();
    this.chatLabels = new Map();
    this.lidToPn = new Map();
    this.pnToLid = new Map();
    this.historyAnchors = new Map();
    this.recentUpdates = [];
    this.loadedPersistedLabelState = false;
    this.persistStateTimer = null;
    this.connectWatchdogTimer = null;
    this.reconnectAttempts = 0;
    this.historyAnchorRefreshPromise = null;
  }

  async start() {
    await fs.mkdir(this.authDir, { recursive: true });
    await this._loadPersistedBridgeState();
    await this._loadPersistedLabelState();
    await this._connect();
    if (!this.listen) {
      return;
    }
    this.server = http.createServer(async (req, res) => {
      try {
        await this._handle(req, res);
      } catch (error) {
        this._writeJson(res, 500, { error: error?.message || String(error) });
      }
    });
    await new Promise((resolve) => {
      this.server.listen(PORT, HOST, resolve);
    });
    console.error(`whatsapp bridge listening on http://${HOST}:${PORT}`);
  }

  async stop() {
    this.stopping = true;
    this._cancelReconnectTimer();
    this._clearConnectWatchdog();
    if (this.persistStateTimer) {
      clearTimeout(this.persistStateTimer);
      this.persistStateTimer = null;
      await this._savePersistedBridgeState();
    }
    if (this.server) {
      await new Promise((resolve) => this.server.close(resolve));
      this.server = null;
    }
    if (this.sock) {
      try {
        this.sock.end(undefined);
      } catch {
        // ignore shutdown errors
      }
      this.sock = null;
    }
  }

  async _connect() {
    if (this.stopping) {
      return;
    }
    this._clearConnectWatchdog();
    if (this.sock) {
      try {
        this.sock.end(undefined);
      } catch {
        // ignore stale socket shutdown errors
      }
      this.sock = null;
    }
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    const { version } = await fetchLatestBaileysVersion();
    const sock = makeWASocket({
      auth: state,
      version,
      browser: Browsers.macOS("Desktop"),
      syncFullHistory: true,
      printQRInTerminal: false,
      markOnlineOnConnect: false,
    });
    this.sock = sock;
    this.saveCreds = saveCreds;
    this.registered = Boolean(sock.authState?.creds?.registered);
    this.connection = "connecting";
    this.connected = false;
    this.lastError = null;
    this._armConnectWatchdog(sock);
    await this._seedChatsFromPersistedMappings(sock.authState?.creds);

    sock.ev.on("creds.update", async () => {
      this.registered = Boolean(sock.authState?.creds?.registered);
      await saveCreds();
    });
    sock.ev.on("connection.update", (update) => this._onConnectionUpdate(sock, update));
    sock.ev.on("messaging-history.set", (event) => this._onHistorySync(event));
    sock.ev.on("contacts.upsert", (contacts) => this._onContacts(contacts));
    sock.ev.on("contacts.update", (contacts) => this._onContacts(contacts));
    sock.ev.on("chats.upsert", (chats) => this._onChats(chats));
    sock.ev.on("chats.update", (updates) => this._onChats(updates));
    sock.ev.on("chats.delete", (jids) => this._onChatsDelete(jids));
    sock.ev.on("messages.upsert", (event) => this._onMessagesUpsert(event));
    sock.ev.on("messages.update", (updates) => this._onMessagesUpdate(updates));
    sock.ev.on("messages.delete", (event) => this._onMessagesDelete(event));
    sock.ev.on("labels.edit", (label) => this._onLabelEdit(label));
    sock.ev.on("labels.association", (event) => this._onLabelAssociation(event));
  }

  async _onConnectionUpdate(sock, update) {
    if (this.sock !== sock) {
      return;
    }
    if (update.qr) {
      this.qrRaw = update.qr;
      try {
        this.qrAscii = await QRCode.toString(update.qr, { type: "terminal", small: true });
        this.qrSvg = await QRCode.toString(update.qr, { type: "svg" });
      } catch (error) {
        this.qrAscii = null;
        this.qrSvg = null;
        this.lastError = error?.message || String(error);
      }
    }
    if (update.connection) {
      this.connection = update.connection;
      this.connected = update.connection === "open";
    }
    if (update.connection === "open") {
      this._clearConnectWatchdog();
      this._resetReconnectState();
      this.me = sock.user
        ? {
            id: sock.user.id || null,
            lid: sock.user.lid || null,
            name: sock.user.name || sock.user.notify || null,
          }
        : null;
      this.registered = Boolean(sock.authState?.creds?.registered);
      this.qrRaw = null;
      this.qrAscii = null;
      this.qrSvg = null;
      this.lastError = null;
      try {
        // Existing labels and chat-label associations live in app-state patches, so
        // we need an initial/full sync here rather than an incremental delta sync.
        await sock.resyncAppState(ALL_WA_PATCH_NAMES, true);
        if (!this.loadedPersistedLabelState && this.labels.size === 0 && this.chatLabels.size === 0) {
          await this._rebuildLabelStateFromScratch(sock);
        }
        await this._ensureHistoryAnchors({
          sock,
          forceSnapshot: this.historyAnchors.size === 0,
        });
      } catch (error) {
        this.lastError = error?.message || String(error);
      }
      await this._savePersistedLabelState();
      await this._seedChatsFromPersistedMappings(sock.authState?.creds);
      return;
    }
    if (update.connection === "close") {
      this._clearConnectWatchdog();
      this.connected = false;
      if (this.sock === sock) {
        this.sock = null;
      }
      const statusCode = update.lastDisconnect?.error?.output?.statusCode;
      if (statusCode === DisconnectReason.loggedOut) {
        this._resetReconnectState();
        this.registered = false;
        this.me = null;
        this.lastError = "WhatsApp session logged out. Scan the next QR code to sign in again.";
        return;
      }
      this.lastError = update.lastDisconnect?.error?.message || "WhatsApp connection closed.";
      this._scheduleReconnect();
    }
  }

  _cancelReconnectTimer() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  _clearConnectWatchdog() {
    if (this.connectWatchdogTimer) {
      clearTimeout(this.connectWatchdogTimer);
      this.connectWatchdogTimer = null;
    }
  }

  _resetReconnectState() {
    this.reconnectAttempts = 0;
    this._cancelReconnectTimer();
  }

  _nextReconnectDelayMs() {
    return Math.min(RECONNECT_DELAY_MS * (2 ** this.reconnectAttempts), MAX_RECONNECT_DELAY_MS);
  }

  _armConnectWatchdog(sock) {
    this._clearConnectWatchdog();
    this.connectWatchdogTimer = setTimeout(() => {
      this.connectWatchdogTimer = null;
      if (this.stopping || this.sock !== sock || this.connected) {
        return;
      }
      this.lastError = "WhatsApp connection timed out. Retrying.";
      this.connection = "close";
      try {
        sock.end(undefined);
      } catch {
        // ignore forced reconnect shutdown errors
      }
      if (this.sock === sock) {
        this.sock = null;
      }
      this._scheduleReconnect();
    }, CONNECT_TIMEOUT_MS);
  }

  _scheduleReconnect() {
    if (this.stopping || this.reconnectTimer) {
      return;
    }
    const delayMs = this._nextReconnectDelayMs();
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      try {
        await this._connect();
      } catch (error) {
        this.lastError = error?.message || String(error);
        this._scheduleReconnect();
      }
    }, delayMs);
  }

  _onHistorySync(event) {
    this._onChats(event?.chats || []);
    this._onContacts(event?.contacts || []);
    for (const message of event?.messages || []) {
      this._upsertMessage(message, false);
    }
  }

  _onContacts(contacts) {
    for (const contact of contacts || []) {
      const jid = firstDefined(contact?.id, contact?.jid);
      if (!jid) {
        continue;
      }
      this.contacts.set(jid, {
        id: jid,
        name: firstDefined(contact?.name, contact?.notify, contact?.verifiedName, contact?.pushName),
      });
    }
    this._schedulePersistedStateSave();
  }

  _onChats(chats) {
    for (const chat of chats || []) {
      const jid = firstDefined(chat?.id, chat?.jid);
      if (!jid) {
        continue;
      }
      const historyMessages = this._extractChatMessages(chat);
      for (const message of historyMessages) {
        const normalized = {
          ...message,
          key: {
            ...message?.key,
            remoteJid: message?.key?.remoteJid || jid,
          },
        };
        this._upsertMessage(normalized, false);
      }
      const latestHistoryMessage = this._mergedChatMessages(jid).at(-1) || null;
      const existing = this.chats.get(jid) || { jid };
      const next = {
        ...existing,
        jid,
        name: firstDefined(chat?.name, existing.name),
        unreadCount: Number.isFinite(chat?.unreadCount) ? chat.unreadCount : existing.unreadCount || 0,
        archived: typeof chat?.archive === "boolean" ? chat.archive : existing.archived || false,
        lastMessageAt: firstDefined(
          latestHistoryMessage?.date,
          timestampToIso(chat?.conversationTimestamp),
          timestampToIso(chat?.lastMessageRecvTimestamp),
          existing.lastMessageAt,
        ),
        lastMessageText: firstDefined(
          latestHistoryMessage?.text,
          existing.lastMessageText,
        ),
      };
      this.chats.set(jid, next);
    }
    this._schedulePersistedStateSave();
  }

  _onChatsDelete(jids) {
    for (const jid of jids || []) {
      this.chats.delete(jid);
      this.messages.delete(jid);
      this.chatLabels.delete(jid);
    }
    this._schedulePersistedStateSave();
    void this._savePersistedLabelState();
  }

  _onMessagesUpsert(event) {
    for (const message of event?.messages || []) {
      this._upsertMessage(message, true);
    }
    this._schedulePersistedStateSave();
  }

  _onMessagesUpdate(updates) {
    for (const item of updates || []) {
      const jid = item?.key?.remoteJid;
      const messageId = item?.key?.id;
      if (!jid || !messageId) {
        continue;
      }
      const entries = this.messages.get(jid) || [];
      const index = entries.findIndex((entry) => entry.id === messageId);
      if (index === -1) {
        continue;
      }
      entries[index] = {
        ...entries[index],
        status: firstDefined(item?.update?.status, entries[index].status),
      };
      this.messages.set(jid, entries);
    }
    this._schedulePersistedStateSave();
  }

  _onMessagesDelete(event) {
    if (event?.all && event?.jid) {
      this.messages.delete(event.jid);
      return;
    }
    for (const key of event?.keys || []) {
      const jid = key?.remoteJid;
      const messageId = key?.id;
      if (!jid || !messageId) {
        continue;
      }
      const entries = (this.messages.get(jid) || []).filter((entry) => entry.id !== messageId);
      this.messages.set(jid, entries);
    }
    this._schedulePersistedStateSave();
  }

  _onLabelEdit(label) {
    if (!label?.id) {
      return;
    }
    this.labels.set(label.id, {
      id: label.id,
      name: label.name || "",
      color: label.color ?? null,
      deleted: Boolean(label.deleted),
      predefinedId: label.predefinedId ?? null,
    });
    void this._savePersistedLabelState();
  }

  _onLabelAssociation(event) {
    const association = event?.association;
    if (!association?.chatId || !association?.labelId || association?.type !== "label_jid") {
      return;
    }
    const labels = this.chatLabels.get(association.chatId) || new Set();
    if (event.type === "remove") {
      labels.delete(association.labelId);
    } else {
      labels.add(association.labelId);
    }
    this.chatLabels.set(association.chatId, labels);
    this._ensureChatRecord(association.chatId);
    void this._savePersistedLabelState();
  }

  _chatAliasJids(jid) {
    const aliases = new Set();
    if (!jid) {
      return aliases;
    }
    aliases.add(jid);
    const user = jidUser(jid);
    if (!user) {
      return aliases;
    }
    if (String(jid).endsWith("@lid")) {
      aliases.add(`${user}@s.whatsapp.net`);
      const pnUser = this.lidToPn.get(user);
      if (pnUser) {
        aliases.add(`${pnUser}@s.whatsapp.net`);
      }
    } else if (String(jid).endsWith("@s.whatsapp.net")) {
      const lidUser = this.pnToLid.get(user);
      if (lidUser) {
        aliases.add(`${lidUser}@lid`);
      }
    }
    return aliases;
  }

  _ensureChatRecord(jid) {
    for (const candidate of this._chatAliasJids(jid)) {
      if (this.chats.has(candidate)) {
        continue;
      }
      this.chats.set(candidate, {
        jid: candidate,
        name: firstDefined(this.contacts.get(candidate)?.name, displayTitleFromJid(candidate)),
        unreadCount: 0,
        archived: false,
        lastMessageAt: null,
        lastMessageText: null,
      });
    }
  }

  _labelStatePath() {
    return path.join(this.authDir, LABEL_STATE_NAME);
  }

  _bridgeStatePath() {
    return path.join(this.authDir, BRIDGE_STATE_NAME);
  }

  _serializeBridgeState() {
    return {
      contacts: [...this.contacts.values()],
      chats: [...this.chats.values()],
      messages: [...this.messages.entries()],
      lid_to_pn: [...this.lidToPn.entries()],
      pn_to_lid: [...this.pnToLid.entries()],
      history_anchors: [...this.historyAnchors.entries()],
    };
  }

  async _loadPersistedBridgeState() {
    try {
      const raw = await fs.readFile(this._bridgeStatePath(), "utf-8");
      const payload = JSON.parse(raw);
      this.contacts.clear();
      this.chats.clear();
      this.messages.clear();
      this.lidToPn.clear();
      this.pnToLid.clear();
      this.historyAnchors.clear();
      for (const contact of payload?.contacts || []) {
        if (!contact?.id) continue;
        this.contacts.set(contact.id, contact);
      }
      for (const chat of payload?.chats || []) {
        if (!chat?.jid) continue;
        this.chats.set(chat.jid, chat);
      }
      for (const [jid, messages] of payload?.messages || []) {
        if (!jid || !Array.isArray(messages)) continue;
        this.messages.set(jid, messages);
      }
      for (const [lidUser, pnUser] of payload?.lid_to_pn || []) {
        if (!lidUser || !pnUser) continue;
        this.lidToPn.set(String(lidUser), String(pnUser));
      }
      for (const [pnUser, lidUser] of payload?.pn_to_lid || []) {
        if (!pnUser || !lidUser) continue;
        this.pnToLid.set(String(pnUser), String(lidUser));
      }
      for (const [jid, anchor] of payload?.history_anchors || []) {
        if (!jid || !anchor?.key?.id) continue;
        this.historyAnchors.set(String(jid), {
          key: {
            ...anchor.key,
            remoteJid: anchor.key.remoteJid || String(jid),
          },
          oldestMsgTimestampMs: Number(anchor.oldestMsgTimestampMs || 0) || null,
        });
      }
    } catch (error) {
      if (error?.code !== "ENOENT") {
        this.lastError = error?.message || String(error);
      }
    }
  }

  async _savePersistedBridgeState() {
    await fs.mkdir(this.authDir, { recursive: true });
    await fs.writeFile(this._bridgeStatePath(), JSON.stringify(this._serializeBridgeState()), "utf-8");
  }

  _schedulePersistedStateSave() {
    if (this.persistStateTimer) {
      clearTimeout(this.persistStateTimer);
    }
    this.persistStateTimer = setTimeout(() => {
      this.persistStateTimer = null;
      void this._savePersistedBridgeState();
    }, STATE_FLUSH_DELAY_MS);
  }

  async _ensureHistoryAnchors({ sock = this.sock, forceSnapshot = false } = {}) {
    if (!sock) {
      return;
    }
    if (!forceSnapshot && this.historyAnchors.size > 0) {
      return;
    }
    if (this.historyAnchorRefreshPromise) {
      return this.historyAnchorRefreshPromise;
    }
    this.historyAnchorRefreshPromise = this._refreshHistoryAnchors(sock, { forceSnapshot })
      .finally(() => {
        this.historyAnchorRefreshPromise = null;
      });
    return this.historyAnchorRefreshPromise;
  }

  async _refreshHistoryAnchors(sock, { forceSnapshot = false } = {}) {
    if (!sock?.authState?.keys || typeof sock.query !== "function") {
      return;
    }
    const authState = sock.authState;
    const initialVersionMap = {};
    const globalMutationMap = {};
    const getAppStateSyncKey = async (keyId) => {
      const result = await authState.keys.get("app-state-sync-key", [keyId]);
      return result[keyId];
    };

    await authState.keys.transaction(async () => {
      if (forceSnapshot) {
        await authState.keys.set({
          "app-state-sync-version": Object.fromEntries(HISTORY_ANCHOR_COLLECTIONS.map((name) => [name, null])),
        });
      }
      const collectionsToHandle = new Set(HISTORY_ANCHOR_COLLECTIONS);
      while (collectionsToHandle.size) {
        const states = {};
        const nodes = [];
        for (const name of collectionsToHandle) {
          const result = await authState.keys.get("app-state-sync-version", [name]);
          let state = result[name];
          if (state) {
            if (typeof initialVersionMap[name] === "undefined") {
              initialVersionMap[name] = state.version;
            }
          } else {
            state = newLTHashState();
          }
          states[name] = state;
          nodes.push({
            tag: "collection",
            attrs: {
              name,
              version: state.version.toString(),
              return_snapshot: (!state.version).toString(),
            },
          });
        }
        const result = await sock.query({
          tag: "iq",
          attrs: {
            to: "s.whatsapp.net",
            xmlns: "w:sync:app:state",
            type: "set",
          },
          content: [
            {
              tag: "sync",
              attrs: {},
              content: nodes,
            },
          ],
        });
        const decoded = await extractSyncdPatches(result, sock?.options);
        for (const name of Object.keys(decoded)) {
          const { patches, hasMorePatches, snapshot } = decoded[name];
          if (snapshot) {
            const { state: newState, mutationMap } = await decodeSyncdSnapshot(
              name,
              snapshot,
              getAppStateSyncKey,
              initialVersionMap[name],
              { value: false, hash: false },
            );
            states[name] = newState;
            Object.assign(globalMutationMap, mutationMap);
            await authState.keys.set({ "app-state-sync-version": { [name]: newState } });
          }
          if (patches.length) {
            const { state: newState, mutationMap } = await decodePatches(
              name,
              patches,
              states[name],
              getAppStateSyncKey,
              sock?.options,
              initialVersionMap[name],
              undefined,
              { value: false, hash: false },
            );
            Object.assign(globalMutationMap, mutationMap);
            await authState.keys.set({ "app-state-sync-version": { [name]: newState } });
          }
          if (!hasMorePatches) {
            collectionsToHandle.delete(name);
          }
        }
      }
    }, authState?.creds?.me?.id || "refresh-history-anchors");

    for (const mutation of Object.values(globalMutationMap)) {
      const index = mutation?.index || mutation?.syncAction?.index;
      const action = mutation?.syncAction?.value;
      const chatJid = Array.isArray(index) ? index[1] : null;
      const messageRange = action?.archiveChatAction?.messageRange || action?.markChatAsReadAction?.messageRange;
      if (!chatJid || !messageRange) {
        continue;
      }
      this._recordHistoryRange(chatJid, messageRange);
    }
    this._schedulePersistedStateSave();
  }

  _recordHistoryRange(chatJid, messageRange) {
    if (!chatJid || !messageRange) {
      return;
    }
    const messages = Array.isArray(messageRange.messages) ? messageRange.messages : [];
    const rangeTimestampIso = timestampToIso(
      messageRange.lastMessageTimestamp || messageRange.lastSystemMessageTimestamp,
    );
    for (const rawMessage of messages) {
      const normalized = {
        ...rawMessage,
        key: {
          ...rawMessage?.key,
          remoteJid: rawMessage?.key?.remoteJid || chatJid,
        },
        messageTimestamp: rawMessage?.messageTimestamp || messageRange.lastMessageTimestamp || messageRange.lastSystemMessageTimestamp || undefined,
      };
      if (normalized.message) {
        this._upsertMessage(normalized, false);
      }
    }

    const anchorSource = messages.at(-1) || messages[0] || null;
    const anchorKey = anchorSource?.key?.id
      ? {
          ...anchorSource.key,
          remoteJid: anchorSource.key.remoteJid || chatJid,
        }
      : null;
    const anchorTimestampMs = timestampToMs(
      anchorSource?.messageTimestamp || messageRange.lastMessageTimestamp || messageRange.lastSystemMessageTimestamp,
    );
    if (!anchorKey || !anchorTimestampMs) {
      return;
    }
    const existingChat = this.chats.get(chatJid) || { jid: chatJid };
    this.chats.set(chatJid, {
      ...existingChat,
      jid: chatJid,
      name: existingChat.name || this.contacts.get(chatJid)?.name || displayTitleFromJid(chatJid),
      unreadCount: existingChat.unreadCount || 0,
      archived: Boolean(existingChat.archived),
      lastMessageAt: existingChat.lastMessageAt || rangeTimestampIso || null,
      lastMessageText: existingChat.lastMessageText || null,
    });
    this.historyAnchors.set(chatJid, {
      key: anchorKey,
      oldestMsgTimestampMs: anchorTimestampMs,
    });
  }

  _historyAnchorForChat(jid) {
    for (const candidate of this._chatAliasJids(jid)) {
      const anchor = this.historyAnchors.get(candidate);
      if (anchor?.key?.id && anchor.oldestMsgTimestampMs) {
        return anchor;
      }
    }
    return null;
  }

  async ensureChatHistory(jid, limit = 50) {
    if (!this._isAllowedChat(jid)) {
      throw new Error(`Blocked access to WhatsApp chat outside ${CLOUD_LABEL_NAME} label`);
    }
    const existingMessages = this._mergedChatMessages(jid);
    if (existingMessages.length > 0) {
      return existingMessages.slice(-limit);
    }
    if (!this.sock || !this.connected) {
      return [];
    }

    let anchor = this._historyAnchorForChat(jid);
    if (!anchor) {
      await this._ensureHistoryAnchors({
        sock: this.sock,
        forceSnapshot: true,
      });
      anchor = this._historyAnchorForChat(jid);
    }
    if (!anchor) {
      return [];
    }

    const beforeCount = this._mergedChatMessages(jid).length;
    await this.sock.fetchMessageHistory(Math.min(Math.max(limit, 1), 50), anchor.key, anchor.oldestMsgTimestampMs);
    const deadline = Date.now() + HISTORY_FETCH_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const messages = this._mergedChatMessages(jid);
      if (messages.length > beforeCount) {
        return messages.slice(-limit);
      }
      await sleep(200);
    }
    return this._mergedChatMessages(jid).slice(-limit);
  }

  _serializeLabelState() {
    return {
      labels: [...this.labels.values()],
      chat_labels: [...this.chatLabels.entries()].map(([chatId, labels]) => ({
        chat_id: chatId,
        label_ids: [...labels],
      })),
    };
  }

  async _loadPersistedLabelState() {
    this.loadedPersistedLabelState = false;
    try {
      const raw = await fs.readFile(this._labelStatePath(), "utf-8");
      const payload = JSON.parse(raw);
      this.labels.clear();
      this.chatLabels.clear();
      for (const label of payload?.labels || []) {
        if (!label?.id) {
          continue;
        }
        this.labels.set(label.id, {
          id: label.id,
          name: label.name || "",
          color: label.color ?? null,
          deleted: Boolean(label.deleted),
          predefinedId: label.predefinedId ?? null,
        });
      }
      for (const association of payload?.chat_labels || []) {
        if (!association?.chat_id || !Array.isArray(association.label_ids)) {
          continue;
        }
        this.chatLabels.set(association.chat_id, new Set(association.label_ids.filter(Boolean)));
        this._ensureChatRecord(association.chat_id);
      }
      this.loadedPersistedLabelState = this.labels.size > 0 || this.chatLabels.size > 0;
    } catch (error) {
      if (error?.code !== "ENOENT") {
        this.lastError = error?.message || String(error);
      }
    }
  }

  async _savePersistedLabelState() {
    await fs.mkdir(this.authDir, { recursive: true });
    if (this.labels.size === 0 && this.chatLabels.size === 0) {
      try {
        const raw = await fs.readFile(this._labelStatePath(), "utf-8");
        const payload = JSON.parse(raw);
        if ((payload?.labels || []).length > 0 || (payload?.chat_labels || []).length > 0) {
          this.loadedPersistedLabelState = true;
          return;
        }
      } catch {
        // no prior label state to preserve
      }
    }
    await fs.writeFile(this._labelStatePath(), JSON.stringify(this._serializeLabelState()), "utf-8");
    this.loadedPersistedLabelState = this.labels.size > 0 || this.chatLabels.size > 0;
  }

  async _rebuildLabelStateFromScratch(sock) {
    await sock.authState.keys.set({
      "app-state-sync-version": Object.fromEntries(ALL_WA_PATCH_NAMES.map((name) => [name, null])),
    });
    this.labels.clear();
    this.chatLabels.clear();
    await sock.resyncAppState(ALL_WA_PATCH_NAMES, true);
  }

  _upsertMessage(message, pushUpdate) {
    const jid = message?.key?.remoteJid;
    const messageId = message?.key?.id;
    if (!jid || !messageId) {
      return null;
    }
    const serialized = this._serializeMessage(message);
    const entries = this.messages.get(jid) || [];
    const existingIndex = entries.findIndex((entry) => entry.id === serialized.id);
    if (existingIndex === -1) {
      entries.push(serialized);
    } else {
      entries[existingIndex] = { ...entries[existingIndex], ...serialized };
    }
    entries.sort((left, right) => {
      const leftTs = Date.parse(left.date || 0);
      const rightTs = Date.parse(right.date || 0);
      return leftTs - rightTs;
    });
    while (entries.length > MAX_CHAT_MESSAGES) {
      entries.shift();
    }
    this.messages.set(jid, entries);

    const existingChat = this.chats.get(jid) || { jid };
    this.chats.set(jid, {
      ...existingChat,
      jid,
      name: firstDefined(existingChat.name, message?.pushName),
      lastMessageAt: serialized.date || existingChat.lastMessageAt || null,
      lastMessageText: serialized.text || existingChat.lastMessageText || null,
    });
    this._schedulePersistedStateSave();

    if (pushUpdate) {
      this._pushRecentUpdate({
        kind: "new_message",
        chat_id: jid,
        message_id: serialized.id,
        message: serialized,
      });
    }

    return serialized;
  }

  _pushRecentUpdate(update) {
    this.recentUpdates.push(update);
    if (this.recentUpdates.length > MAX_UPDATES) {
      this.recentUpdates.splice(0, this.recentUpdates.length - MAX_UPDATES);
    }
  }

  _serializeMessage(message) {
    return {
      id: message.key?.id || null,
      chat_id: message.key?.remoteJid || null,
      participant: message.key?.participant || null,
      from_me: Boolean(message.key?.fromMe),
      text: extractText(message),
      kind: messageKind(message),
      date: timestampToIso(message.messageTimestamp),
      status: message.status ?? null,
    };
  }

  _cloudLabelRecord() {
    const wanted = CLOUD_LABEL_NAME.toLowerCase();
    for (const label of this.labels.values()) {
      if (!label.deleted && String(label.name || "").trim().toLowerCase() === wanted) {
        return label;
      }
    }
    return null;
  }

  _cloudFilterMode() {
    return this._cloudLabelRecord() ? "cloud-label" : "label-required";
  }

  async _seedChatsFromPersistedMappings(creds = null) {
    let entries = [];
    try {
      entries = await fs.readdir(this.authDir);
    } catch {
      return;
    }

    const mappingEntries = [];
    for (const entry of entries) {
      const match = /^lid-mapping-(.+)\.json$/.exec(entry);
      if (!match || match[1].endsWith("_reverse")) {
        continue;
      }
      try {
        const raw = await fs.readFile(path.join(this.authDir, entry), "utf-8");
        mappingEntries.push([match[1], JSON.parse(raw)]);
      } catch {
        // ignore malformed persisted mapping files
      }
    }

    this.lidToPn.clear();
    this.pnToLid.clear();
    for (const [pnUser, lidUser] of mappingEntries) {
      if (!pnUser || !lidUser) {
        continue;
      }
      const normalizedPn = String(pnUser);
      const normalizedLid = String(lidUser);
      this.pnToLid.set(normalizedPn, normalizedLid);
      this.lidToPn.set(normalizedLid, normalizedPn);
    }

    const peerJids = peerJidsFromLidMappings(mappingEntries, {
      meId: creds?.me?.id || this.me?.id || null,
      meLid: creds?.me?.lid || this.me?.lid || null,
    });

    for (const jid of peerJids) {
      const existing = this.chats.get(jid) || { jid };
      this.chats.set(jid, {
        ...existing,
        jid,
        name: firstDefined(existing.name, this.contacts.get(jid)?.name, displayTitleFromJid(jid)),
        unreadCount: existing.unreadCount || 0,
        archived: Boolean(existing.archived),
        lastMessageAt: existing.lastMessageAt || null,
        lastMessageText: existing.lastMessageText || null,
      });
    }
    this._schedulePersistedStateSave();
  }

  _isAllowedChat(jid) {
    const label = this._cloudLabelRecord();
    if (!label) {
      return false;
    }
    for (const candidate of this._chatAliasJids(jid)) {
      if (this.chatLabels.get(candidate)?.has(label.id)) {
        return true;
      }
    }
    return false;
  }

  _chatTitle(jid, chat) {
    return firstDefined(chat?.name, this.contacts.get(jid)?.name, displayTitleFromJid(jid), jid);
  }

  _canonicalAllowedChatJid(jid) {
    if (String(jid).endsWith("@lid")) {
      const pnUser = this.lidToPn.get(jidUser(jid));
      if (pnUser) {
        const pnJid = `${pnUser}@s.whatsapp.net`;
        if (this.chats.has(pnJid)) {
          return pnJid;
        }
      }
    }
    return jid;
  }

  _serializeChat(jid, chat) {
    const labelIds = new Set();
    for (const candidate of this._chatAliasJids(jid)) {
      for (const labelId of this.chatLabels.get(candidate) || []) {
        labelIds.add(labelId);
      }
    }
    const labels = [...labelIds]
      .map((labelId) => this.labels.get(labelId))
      .filter(Boolean)
      .map((label) => label.name);
    return {
      jid,
      title: this._chatTitle(jid, chat),
      kind: jidKind(jid),
      unread_count: chat?.unreadCount || 0,
      archived: Boolean(chat?.archived),
      last_message_at: chat?.lastMessageAt || null,
      last_message_text: chat?.lastMessageText || null,
      labels,
    };
  }

  _allowedChats(limit = 200) {
    const unique = new Map();
    for (const [jid, chat] of this.chats.entries()) {
      if (!this._isAllowedChat(jid)) {
        continue;
      }
      const canonicalJid = this._canonicalAllowedChatJid(jid);
      if (!unique.has(canonicalJid)) {
        unique.set(canonicalJid, this._serializeChat(canonicalJid, this.chats.get(canonicalJid) || chat));
      }
    }
    const chats = [...unique.values()]
      .sort((left, right) => {
        const leftTs = Date.parse(left.last_message_at || 0);
        const rightTs = Date.parse(right.last_message_at || 0);
        return rightTs - leftTs;
      });
    return chats.slice(0, limit);
  }

  _extractChatMessages(chat) {
    const lastMessages = chat?.lastMessages;
    if (Array.isArray(lastMessages)) {
      return lastMessages;
    }
    if (Array.isArray(lastMessages?.messages)) {
      return lastMessages.messages;
    }
    return [];
  }

  _mergedChatMessages(jid) {
    const merged = new Map();
    for (const candidate of this._chatAliasJids(jid)) {
      for (const message of this.messages.get(candidate) || []) {
        if (!message?.id) {
          continue;
        }
        const key = `${message.chat_id || candidate}:${message.id}`;
        merged.set(key, message);
      }
    }
    return [...merged.values()].sort((left, right) => {
      const leftTs = Date.parse(left.date || 0);
      const rightTs = Date.parse(right.date || 0);
      if (leftTs !== rightTs) {
        return leftTs - rightTs;
      }
      return String(left.id || "").localeCompare(String(right.id || ""));
    });
  }

  _chatMessages(jid, limit = 50) {
    if (!this._isAllowedChat(jid)) {
      throw new Error(`Blocked access to WhatsApp chat outside ${CLOUD_LABEL_NAME} label`);
    }
    const messages = this._mergedChatMessages(jid);
    return messages.slice(-limit);
  }

  _status(limit = 200) {
    const cloudLabel = this._cloudLabelRecord();
    const hasSession = this.registered || this.connected || Boolean(this.me);
    return {
      ok: true,
      connection: this.connection,
      connected: this.connected,
      has_session: hasSession,
      authorized: hasSession,
      qr_available: Boolean(this.qrRaw),
      qr_raw: this.qrRaw,
      qr_ascii: this.qrAscii,
      qr_svg: this.qrSvg,
      cloud_label_name: CLOUD_LABEL_NAME,
      cloud_label_found: Boolean(cloudLabel),
      cloud_label: cloudLabel,
      cloud_filter_mode: this._cloudFilterMode(),
      auth_dir: this.authDir,
      me: this.me,
      started_at: this.startedAt,
      last_error: this.lastError,
      chats: this._allowedChats(limit),
      update_count: this.recentUpdates.length,
    };
  }

  async authStatus() {
    if (!this.sock) {
      await this._connect();
    }
    return this._status();
  }

  async logout() {
    if (this.sock) {
      try {
        await this.sock.logout();
      } catch {
        // ignore remote logout failures, local cleanup still matters
      }
    }
    await fs.rm(this.authDir, { recursive: true, force: true });
    await fs.mkdir(this.authDir, { recursive: true });
    this.registered = false;
    this.connected = false;
    this.connection = "idle";
    this.me = null;
    this.qrRaw = null;
    this.qrAscii = null;
    this.qrSvg = null;
    this.lastError = null;
    this.contacts.clear();
    this.chats.clear();
    this.messages.clear();
    this.labels.clear();
    this.chatLabels.clear();
    this.recentUpdates = [];
    await fs.rm(this._bridgeStatePath(), { force: true });
    await this._connect();
    return this._status();
  }

  async sendMessage(jid, text) {
    if (!this.sock) {
      throw new Error("WhatsApp socket is not connected yet");
    }
    if (!this._isAllowedChat(jid)) {
      throw new Error(`Blocked send to WhatsApp chat outside ${CLOUD_LABEL_NAME} label`);
    }
    if (!String(text || "").trim()) {
      throw new Error("Message text is required");
    }
    const sent = await this.sock.sendMessage(jid, { text: String(text) });
    if (!sent) {
      throw new Error("WhatsApp sendMessage returned no message payload");
    }
    const serialized = this._upsertMessage(sent, true);
    return { ok: true, message: serialized };
  }

  async markRead(jid, messageId = null) {
    if (!this.sock) {
      throw new Error("WhatsApp socket is not connected yet");
    }
    if (!this._isAllowedChat(jid)) {
      throw new Error(`Blocked read receipt for WhatsApp chat outside ${CLOUD_LABEL_NAME} label`);
    }
    const messages = this.messages.get(jid) || [];
    const target = messageId
      ? messages.find((message) => message.id === messageId)
      : messages[messages.length - 1];
    if (!target?.id) {
      return { ok: true, marked: false };
    }
    await this.sock.readMessages([
      {
        remoteJid: jid,
        id: target.id,
        fromMe: Boolean(target.from_me),
        participant: target.participant || undefined,
      },
    ]);
    return { ok: true, marked: true, message_id: target.id };
  }

  _parseUrl(req) {
    return new URL(req.url || "/", `http://${HOST}:${PORT}`);
  }

  async _readJson(req) {
    const chunks = [];
    for await (const chunk of req) {
      chunks.push(chunk);
    }
    if (!chunks.length) {
      return {};
    }
    const body = Buffer.concat(chunks).toString("utf-8");
    return body ? JSON.parse(body) : {};
  }

  async _handle(req, res) {
    const url = this._parseUrl(req);
    if (req.method === "GET" && url.pathname === "/health") {
      this._writeJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/status") {
      this._writeJson(res, 200, this._status(Number.parseInt(url.searchParams.get("limit") || "200", 10)));
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/chats") {
      this._writeJson(res, 200, { ok: true, chats: this._allowedChats(Number.parseInt(url.searchParams.get("limit") || "200", 10)) });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/chat") {
      const jid = String(url.searchParams.get("jid") || "").trim();
      const limit = Number.parseInt(url.searchParams.get("limit") || "50", 10);
      this._writeJson(res, 200, {
        ok: true,
        chat: this._serializeChat(jid, this.chats.get(jid)),
        messages: this._chatMessages(jid, limit),
      });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/updates") {
      const limit = Number.parseInt(url.searchParams.get("limit") || "50", 10);
      const updates = this.recentUpdates
        .filter((update) => this._isAllowedChat(update.chat_id))
        .slice(-limit);
      this._writeJson(res, 200, { ok: true, updates });
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/auth/request-pairing-code") {
      this._writeJson(res, 200, await this.authStatus());
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/auth/logout") {
      this._writeJson(res, 200, await this.logout());
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/send-message") {
      const payload = await this._readJson(req);
      this._writeJson(res, 200, await this.sendMessage(String(payload.jid || "").trim(), payload.text));
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/mark-read") {
      const payload = await this._readJson(req);
      this._writeJson(
        res,
        200,
        await this.markRead(String(payload.jid || "").trim(), payload.message_id ? String(payload.message_id) : null),
      );
      return;
    }
    this._writeJson(res, 404, { error: "Not Found" });
  }

  _writeJson(res, status, payload) {
    const body = Buffer.from(JSON.stringify(payload));
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": String(body.length),
      "Cache-Control": "no-store",
    });
    res.end(body);
  }
}

const service = new WhatsAppBridgeService();

process.on("SIGINT", async () => {
  await service.stop();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  await service.stop();
  process.exit(0);
});

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await service.start();
}
