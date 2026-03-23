import http from "node:http";
import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import QRCode from "qrcode";
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";

const HOST = process.env.TP_WHATSAPP_HOST || "127.0.0.1";
const PORT = Number.parseInt(process.env.TP_WHATSAPP_PORT || "8792", 10);
const CLOUD_LABEL_NAME = (process.env.TP_WHATSAPP_CLOUD_LABEL || "Cloud").trim() || "Cloud";
const AUTH_DIR = path.resolve(
  process.env.TP_WHATSAPP_AUTH_DIR || path.join(process.env.HOME || process.cwd(), ".tlt-proxy", "whatsapp-auth"),
);
const MAX_CHAT_MESSAGES = 200;
const MAX_UPDATES = 500;
const RECONNECT_DELAY_MS = 2_000;
const APP_STATE_COLLECTIONS = ["critical_block", "critical_unblock_low", "regular", "regular_low", "regular_high"];

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

class WhatsAppBridgeService {
  constructor() {
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
    this.recentUpdates = [];
  }

  async start() {
    await fs.mkdir(AUTH_DIR, { recursive: true });
    await this._connect();
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
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
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
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
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
    this.lastError = null;
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
        await sock.resyncAppState(APP_STATE_COLLECTIONS, false);
      } catch (error) {
        this.lastError = error?.message || String(error);
      }
      await this._seedChatsFromPersistedMappings(sock.authState?.creds);
      return;
    }
    if (update.connection === "close") {
      this.connected = false;
      const statusCode = update.lastDisconnect?.error?.output?.statusCode;
      if (statusCode === DisconnectReason.loggedOut) {
        this.registered = false;
        this.me = null;
        this.lastError = "WhatsApp session logged out. Scan the next QR code to sign in again.";
        return;
      }
      this.lastError = update.lastDisconnect?.error?.message || "WhatsApp connection closed.";
      this._scheduleReconnect();
    }
  }

  _scheduleReconnect() {
    if (this.stopping || this.reconnectTimer) {
      return;
    }
    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      try {
        await this._connect();
      } catch (error) {
        this.lastError = error?.message || String(error);
        this._scheduleReconnect();
      }
    }, RECONNECT_DELAY_MS);
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
  }

  _onChats(chats) {
    for (const chat of chats || []) {
      const jid = firstDefined(chat?.id, chat?.jid);
      if (!jid) {
        continue;
      }
      const existing = this.chats.get(jid) || { jid };
      const next = {
        ...existing,
        jid,
        name: firstDefined(chat?.name, existing.name),
        unreadCount: Number.isFinite(chat?.unreadCount) ? chat.unreadCount : existing.unreadCount || 0,
        archived: typeof chat?.archive === "boolean" ? chat.archive : existing.archived || false,
        lastMessageAt: firstDefined(
          timestampToIso(chat?.conversationTimestamp),
          timestampToIso(chat?.lastMessageRecvTimestamp),
          existing.lastMessageAt,
        ),
      };
      this.chats.set(jid, next);
    }
  }

  _onChatsDelete(jids) {
    for (const jid of jids || []) {
      this.chats.delete(jid);
      this.messages.delete(jid);
      this.chatLabels.delete(jid);
    }
  }

  _onMessagesUpsert(event) {
    for (const message of event?.messages || []) {
      this._upsertMessage(message, true);
    }
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
    return this._cloudLabelRecord() ? "cloud-label" : "all-chats-fallback";
  }

  async _seedChatsFromPersistedMappings(creds = null) {
    let entries = [];
    try {
      entries = await fs.readdir(AUTH_DIR);
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
        const raw = await fs.readFile(path.join(AUTH_DIR, entry), "utf-8");
        mappingEntries.push([match[1], JSON.parse(raw)]);
      } catch {
        // ignore malformed persisted mapping files
      }
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
  }

  _isAllowedChat(jid) {
    const label = this._cloudLabelRecord();
    if (!label) {
      return this.chats.has(jid);
    }
    return Boolean(this.chatLabels.get(jid)?.has(label.id));
  }

  _chatTitle(jid, chat) {
    return firstDefined(chat?.name, this.contacts.get(jid)?.name, displayTitleFromJid(jid), jid);
  }

  _serializeChat(jid, chat) {
    const labels = [...(this.chatLabels.get(jid) || [])]
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
    const chats = [...this.chats.entries()]
      .filter(([jid]) => this._isAllowedChat(jid))
      .map(([jid, chat]) => this._serializeChat(jid, chat))
      .sort((left, right) => {
        const leftTs = Date.parse(left.last_message_at || 0);
        const rightTs = Date.parse(right.last_message_at || 0);
        return rightTs - leftTs;
      });
    return chats.slice(0, limit);
  }

  _chatMessages(jid, limit = 50) {
    if (!this._isAllowedChat(jid)) {
      throw new Error(`Blocked access to WhatsApp chat outside ${CLOUD_LABEL_NAME} label`);
    }
    const messages = this.messages.get(jid) || [];
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
      auth_dir: AUTH_DIR,
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
    await fs.rm(AUTH_DIR, { recursive: true, force: true });
    await fs.mkdir(AUTH_DIR, { recursive: true });
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
