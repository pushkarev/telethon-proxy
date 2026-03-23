import { execFileSync } from "node:child_process";

import { Api, TelegramClient, utils } from "telegram";
import { StringSession } from "telegram/sessions/index.js";


const DASHBOARD_HOST = process.env.TP_DASHBOARD_HOST || "127.0.0.1";
const DASHBOARD_PORT = Number(process.env.TP_DASHBOARD_PORT || "8788");
const KEYCHAIN_SERVICE = "dev.telethon-proxy.telegram";
const CLOUD_FOLDER_NAME = process.env.TP_CLOUD_FOLDER || "Cloud";

const UPSTREAM_API_ID_ACCOUNT = "upstream_api_id";
const UPSTREAM_API_HASH_ACCOUNT = "upstream_api_hash";
const UPSTREAM_PHONE_ACCOUNT = "upstream_phone";
const UPSTREAM_SESSION_ACCOUNT = "upstream_session";


export function serializeDate(value) {
  if (value == null || value === "") return null;
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "number" && Number.isFinite(value)) {
    const millis = value >= 1_000_000_000_000 ? value : value * 1000;
    return new Date(millis).toISOString();
  }
  if (typeof value === "bigint") {
    const millis = value >= 1_000_000_000_000n ? value : value * 1000n;
    return new Date(Number(millis)).toISOString();
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? null : new Date(parsed).toISOString();
  }
  if (typeof value.toISOString === "function") return value.toISOString();
  return null;
}

export function applyPublicDashboardConfig(payload) {
  if (!payload || typeof payload !== "object") return payload;
  if (!payload.config || typeof payload.config !== "object") {
    payload.config = {};
  }
  payload.config.dashboard_host = DASHBOARD_HOST;
  payload.config.dashboard_port = DASHBOARD_PORT;
  return payload;
}

function toTitleText(title) {
  if (!title) return "";
  if (typeof title === "string") return title;
  if (typeof title.text === "string") return title.text;
  if (Array.isArray(title)) {
    return title.map((part) => toTitleText(part)).join("");
  }
  return "";
}

function getSecurityAccount(account) {
  try {
    return execFileSync(
      "security",
      ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
      { encoding: "utf8" },
    ).trim();
  } catch {
    return "";
  }
}

function loadTelegramSecrets() {
  return {
    apiId: getSecurityAccount(UPSTREAM_API_ID_ACCOUNT) || String(process.env.TG_API_ID || "").trim(),
    apiHash: getSecurityAccount(UPSTREAM_API_HASH_ACCOUNT) || String(process.env.TG_API_HASH || "").trim(),
    phone: getSecurityAccount(UPSTREAM_PHONE_ACCOUNT) || String(process.env.TG_PHONE || "").trim(),
    session: getSecurityAccount(UPSTREAM_SESSION_ACCOUNT) || String(process.env.TP_UPSTREAM_SESSION_STRING || "").trim(),
  };
}

export function dialogMatchesFilter(dialog, dialogFilter, includedPeerIds, excludedPeerIds) {
  const dialogPeerId = utils.getPeerId(dialog.entity);
  if (excludedPeerIds.has(dialogPeerId)) return false;
  if (includedPeerIds.has(dialogPeerId)) return true;

  const entity = dialog.entity;
  const isUser = Boolean(dialog.isUser);
  const isGroup = Boolean(dialog.isGroup);
  const isBroadcast = Boolean(dialog.isChannel) && !Boolean(dialog.isGroup);
  const isBot = Boolean(entity?.bot);
  const isContact = Boolean(entity?.contact);
  const isNonContact = isUser && !isContact;
  const archived = dialog.folderId === 1 || dialog.dialog?.folderId === 1;
  const notifySettings = dialog.dialog?.notifySettings;
  const muteUntil = notifySettings?.muteUntil;

  if (dialogFilter.contacts && !isContact) return false;
  if (dialogFilter.nonContacts && !isNonContact) return false;
  if (dialogFilter.groups && !isGroup) return false;
  if (dialogFilter.broadcasts && !isBroadcast) return false;
  if (dialogFilter.bots && !isBot) return false;
  if (dialogFilter.excludeMuted && muteUntil != null) return false;
  if (dialogFilter.excludeRead && Number(dialog.unreadCount || 0) === 0) return false;
  if (dialogFilter.excludeArchived && archived) return false;

  const hasPositiveRule = [
    dialogFilter.contacts,
    dialogFilter.nonContacts,
    dialogFilter.groups,
    dialogFilter.broadcasts,
    dialogFilter.bots,
  ].some(Boolean);
  return hasPositiveRule;
}

export class GramJsTelegramBridge {
  constructor({ folderName = CLOUD_FOLDER_NAME } = {}) {
    this.folderName = folderName;
    this.client = null;
    this.clientSignature = "";
    this.cachedDialogs = [];
    this.cachedAt = 0;
    this.cachedIdentity = null;
  }

  async stop() {
    if (!this.client) return;
    try {
      await this.client.disconnect();
    } catch {
      // Ignore shutdown errors.
    }
    this.client = null;
    this.clientSignature = "";
    this.cachedDialogs = [];
    this.cachedAt = 0;
    this.cachedIdentity = null;
  }

  async getOverviewChats() {
    const dialogs = await this._getAllowedDialogs();
    return dialogs.map((dialog) => this._serializeDialog(dialog));
  }

  async getIdentity() {
    await this._ensureClient();
    if (this.cachedIdentity) {
      return this.cachedIdentity;
    }
    const me = await this.client.getMe();
    this.cachedIdentity = {
      id: Number(me?.id || 0) || null,
      name: [me?.firstName, me?.lastName].filter(Boolean).join(" ").trim() || me?.username || "Telegram account",
      phone: me?.phone || null,
      username: me?.username || null,
    };
    return this.cachedIdentity;
  }

  async getChat(peerId, limit = 50) {
    const dialogs = await this._getAllowedDialogs();
    const target = dialogs.find((dialog) => String(utils.getPeerId(dialog.entity)) === String(peerId));
    if (!target) {
      return { chat: null, messages: [] };
    }
    const messages = await this.client.getMessages(target.inputEntity ?? target.entity, { limit });
    return {
      chat: this._serializeDialog(target),
      messages: [...messages].map((message) => this._serializeMessage(message)),
    };
  }

  async _ensureClient() {
    const secrets = loadTelegramSecrets();
    const signature = `${secrets.apiId}|${secrets.apiHash}|${secrets.session}`;
    if (!secrets.apiId || !secrets.apiHash || !secrets.session) {
      await this.stop();
      throw new Error("Telegram authentication is required in Telegram -> Settings");
    }
    if (this.client && this.clientSignature === signature) {
      if (this.client.connected) {
        return;
      }
      await this.client.connect();
      if (!(await this.client.checkAuthorization())) {
        throw new Error("Telegram authentication is required in Telegram -> Settings");
      }
      return;
    }

    await this.stop();
    this.client = new TelegramClient(
      new StringSession(secrets.session),
      Number(secrets.apiId),
      secrets.apiHash,
      { connectionRetries: 1 },
    );
    this.clientSignature = signature;
    await this.client.connect();
    if (!(await this.client.checkAuthorization())) {
      throw new Error("Telegram authentication is required in Telegram -> Settings");
    }
    this.cachedAt = 0;
    this.cachedDialogs = [];
    this.cachedIdentity = null;
  }

  async _getAllowedDialogs() {
    await this._ensureClient();
    const now = Date.now();
    if (this.cachedDialogs.length && now - this.cachedAt < 5000) {
      return this.cachedDialogs;
    }
    const filtersResult = await this.client.invoke(new Api.messages.GetDialogFilters());
    let targetFilter = null;
    let includedPeerIds = new Set();
    let excludedPeerIds = new Set();

    for (const dialogFilter of filtersResult.filters || []) {
      if (!(dialogFilter instanceof Api.DialogFilter)) continue;
      if (toTitleText(dialogFilter.title) !== this.folderName) continue;
      targetFilter = dialogFilter;
      includedPeerIds = new Set([
        ...(dialogFilter.includePeers || []).map((peer) => utils.getPeerId(peer)),
        ...(dialogFilter.pinnedPeers || []).map((peer) => utils.getPeerId(peer)),
      ]);
      excludedPeerIds = new Set((dialogFilter.excludePeers || []).map((peer) => utils.getPeerId(peer)));
      break;
    }

    if (!targetFilter) {
      throw new Error(`Dialog folder '${this.folderName}' not found`);
    }

    const dialogs = await this.client.getDialogs({ limit: undefined });
    const allowed = dialogs.filter((dialog) => dialogMatchesFilter(dialog, targetFilter, includedPeerIds, excludedPeerIds));
    this.cachedDialogs = allowed;
    this.cachedAt = now;
    return allowed;
  }

  _serializeDialog(dialog) {
    const peerId = Number(utils.getPeerId(dialog.entity));
    return {
      peer_id: peerId,
      title: dialog.title,
      username: dialog.entity?.username || null,
      kind: dialog.isUser ? "dm" : (dialog.isGroup ? "group" : (dialog.isChannel ? "channel" : "chat")),
      last_message_at: serializeDate(dialog.date),
    };
  }

  _serializeMessage(message) {
    return {
      id: Number(message.id),
      text: message.message || "",
      date: serializeDate(message.date),
      out: Boolean(message.out),
      media: message.media ? message.media.className || message.media.constructor?.name || "media" : null,
    };
  }
}
