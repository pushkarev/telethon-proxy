import http from "node:http";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import { randomBytes } from "node:crypto";
import { execFileSync, execFile } from "node:child_process";
import { promisify } from "node:util";

import { Api, TelegramClient, utils } from "telegram";
import { StringSession } from "telegram/sessions/index.js";

import { GramJsTelegramBridge } from "./gramjs-background.mjs";
import { WhatsAppBridgeService } from "../whatsapp-project/service.mjs";


const execFileAsync = promisify(execFile);

const DEFAULT_CONFIG_HOME = path.join(os.homedir(), ".tlt-proxy");
const DEFAULT_ENV_PATH = path.join(DEFAULT_CONFIG_HOME, ".env");
const KEYCHAIN_SERVICE = "dev.telethon-proxy.telegram";
const UPSTREAM_API_ID_ACCOUNT = "upstream_api_id";
const UPSTREAM_API_HASH_ACCOUNT = "upstream_api_hash";
const UPSTREAM_PHONE_ACCOUNT = "upstream_phone";
const UPSTREAM_SESSION_ACCOUNT = "upstream_session";
const MCP_TOKEN_ACCOUNT = "mcp_token";
const APPLE_EPOCH_OFFSET = 978307200;
const FIELD_SEPARATOR = "\t";
const LIST_SEPARATOR = "\x1f";
const DEFAULT_MESSAGES_DB = path.join(os.homedir(), "Library", "Messages", "chat.db");
const ACCOUNT_STATUS_SCRIPT = `
on replaceText(subjectText, searchText, replacementText)
  set AppleScript's text item delimiters to searchText
  set textItems to every text item of subjectText
  set AppleScript's text item delimiters to replacementText
  set joinedText to textItems as text
  set AppleScript's text item delimiters to ""
  return joinedText
end replaceText

on sanitizeText(valueText)
  try
    set cleanValue to valueText as text
  on error
    return ""
  end try
  set cleanValue to my replaceText(cleanValue, tab, " ")
  set cleanValue to my replaceText(cleanValue, return, " ")
  set cleanValue to my replaceText(cleanValue, linefeed, " ")
  return cleanValue
end sanitizeText

tell application "Messages"
  set outputLines to {}
  repeat with svc in every account
    try
      set end of outputLines to (my sanitizeText(id of svc)) & "${FIELD_SEPARATOR}" & (my sanitizeText(connection status of svc as text)) & "${FIELD_SEPARATOR}" & ((enabled of svc) as text) & "${FIELD_SEPARATOR}" & (my sanitizeText(description of svc)) & "${FIELD_SEPARATOR}" & (my sanitizeText(service type of svc as text))
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  set outputText to outputLines as text
  set AppleScript's text item delimiters to ""
  return outputText
end tell
`;
const CHAT_LIST_SCRIPT = `
on replaceText(subjectText, searchText, replacementText)
  set AppleScript's text item delimiters to searchText
  set textItems to every text item of subjectText
  set AppleScript's text item delimiters to replacementText
  set joinedText to textItems as text
  set AppleScript's text item delimiters to ""
  return joinedText
end replaceText

on sanitizeText(valueText)
  try
    set cleanValue to valueText as text
  on error
    return ""
  end try
  set cleanValue to my replaceText(cleanValue, tab, " ")
  set cleanValue to my replaceText(cleanValue, return, " ")
  set cleanValue to my replaceText(cleanValue, linefeed, " ")
  return cleanValue
end sanitizeText

tell application "Messages"
  set outputLines to {}
  set chatIndex to 0
  repeat with c in every chat
    try
      set chatIndex to chatIndex + 1
      set participantHandles to {}
      repeat with p in every participant of c
        set end of participantHandles to my sanitizeText(handle of p)
      end repeat
      set AppleScript's text item delimiters to "${LIST_SEPARATOR}"
      set participantText to participantHandles as text
      set AppleScript's text item delimiters to ""
      set chatName to ""
      try
        set chatName to my sanitizeText(name of c)
      end try
      set end of outputLines to (my sanitizeText(id of c)) & "${FIELD_SEPARATOR}" & chatName & "${FIELD_SEPARATOR}" & participantText & "${FIELD_SEPARATOR}" & ((count of participants of c) as text) & "${FIELD_SEPARATOR}" & (my sanitizeText(id of account of c)) & "${FIELD_SEPARATOR}" & (chatIndex as text) & "${FIELD_SEPARATOR}" & (my sanitizeText(service type of (account of c) as text))
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  set outputText to outputLines as text
  set AppleScript's text item delimiters to ""
  return outputText
end tell
`;


function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const [name, ...rest] = line.split("=");
    if (!process.env[name.trim()]) {
      process.env[name.trim()] = rest.join("=").trim();
    }
  }
}

function loadProjectEnv() {
  ensureDir(DEFAULT_CONFIG_HOME);
  parseEnvFile(process.env.TG_ENV_FILE || DEFAULT_ENV_PATH);
}

function firstNonEmpty(...values) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
}

function nowIso() {
  return new Date().toISOString();
}

function truthy(value) {
  return ["1", "true", "yes"].includes(String(value ?? "").trim().toLowerCase());
}

function cleanScriptValue(value) {
  const text = String(value ?? "").trim();
  return text.toLowerCase() === "missing value" ? "" : text;
}

function appleMessageDateToIso(value) {
  if (value == null || value === "" || value === 0) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric === 0) return null;
  const seconds = Math.abs(numeric) > 1e12 ? numeric / 1e9 : numeric;
  return new Date((seconds + APPLE_EPOCH_OFFSET) * 1000).toISOString();
}

function sqlQuote(value) {
  return `'${String(value ?? "").replaceAll("'", "''")}'`;
}

function decodeAttributedBody(hexString) {
  if (!hexString) return { text: null, url: null };
  let content = "";
  try {
    content = Buffer.from(String(hexString), "hex").toString("utf8");
  } catch {
    return { text: null, url: null };
  }
  const textPatterns = [
    /NSString">(.*?)</s,
    /NSString">([^<]+)/s,
    /NSNumber">\d+<.*?NSString">(.*?)</s,
    /NSArray">.*?NSString">(.*?)</s,
    /"string":\s*"([^"]+)"/s,
    /text[^>]*>(.*?)</s,
    /message>(.*?)</s,
  ];
  let text = null;
  for (const pattern of textPatterns) {
    const match = content.match(pattern);
    if (match?.[1]?.trim()) {
      const candidate = match[1].split(/\s+/).join(" ");
      if (candidate.length > 3) {
        text = candidate;
        break;
      }
    }
  }
  const urlPatterns = [
    /(https?:\/\/[^\s<"]+)/s,
    /NSString">(https?:\/\/[^\s<"]+)/s,
    /"url":\s*"(https?:\/\/[^"]+)"/s,
    /link[^>]*>(https?:\/\/[^<]+)/s,
  ];
  let url = null;
  for (const pattern of urlPatterns) {
    const match = content.match(pattern);
    if (match?.[1]?.trim()) {
      url = match[1].trim();
      break;
    }
  }
  const metadataTokens = new Set([
    "streamtyped",
    "nsattributedstring",
    "nsmutableattributedstring",
    "nsdictionary",
    "nsnumber",
    "nsobject",
    "nsmutablestring",
    "nsstring",
    "nsvalue",
  ]);
  const metadataPrefixes = ["__kIM", "kIM", "NSDictionary", "NSNumber", "NSObject", "NSAttributed", "NSMutable", "NSString", "NSValue"];
  const candidateChunks = [];
  const normalized = content.replace(/[^\w\s\.,!?@:/+\-()\u0400-\u04FF\u2018\u2019\u201C\u201D\p{Emoji_Presentation}]+/gu, "\n");
  for (const rawChunk of normalized.split("\n")) {
    let candidate = rawChunk.split(/\s+/).join(" ").trim().replace(/^[ +<>.,!?:;*\-_]+|[ +<>.,!?:;*\-_]+$/g, "");
    if (candidate.length < 2) continue;
    const lowered = candidate.toLowerCase();
    if (metadataTokens.has(lowered)) continue;
    if (metadataPrefixes.some((prefix) => lowered.startsWith(prefix.toLowerCase()))) continue;
    if (lowered.includes("__kim") || lowered.includes("kimmessagepartattribute")) continue;
    if (!/[A-Za-z\u0400-\u04FF]/.test(candidate)) continue;
    candidate = candidate.replace(/^[A-Z](?=[A-Z][a-z])/, "");
    let score = candidate.length;
    if (candidate.includes(" ")) score += 8;
    if (/[\u0400-\u04FF]/.test(candidate)) score += 8;
    if (/[.!?]/.test(candidate)) score += 4;
    candidateChunks.push([score, candidate]);
  }
  if (candidateChunks.length) {
    candidateChunks.sort((a, b) => b[0] - a[0]);
    text = candidateChunks[0][1];
  }
  if (!text) {
    const readable = content.replace(/[^\x20-\x7E]/g, " ").replace(/\s+/g, " ").trim();
    if (readable.length > 3) text = readable;
  }
  if (text) {
    text = text.replace(/^[+\s]+/, "").replace(/\s+/g, " ").trim();
  }
  return { text: text || null, url };
}

class NativeSecretStore {
  constructor(service = KEYCHAIN_SERVICE) {
    this.service = service;
  }

  get isAvailable() {
    return process.platform === "darwin";
  }

  get(account) {
    if (!this.isAvailable) return null;
    try {
      return execFileSync("security", ["find-generic-password", "-s", this.service, "-a", account, "-w"], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
      }).replace(/\n$/, "");
    } catch (error) {
      const stderr = String(error.stderr || "").trim().toLowerCase();
      if (stderr.includes("could not be found")) return null;
      throw error;
    }
  }

  set(account, value) {
    if (!this.isAvailable) throw new Error("macOS Keychain is only available on macOS");
    execFileSync("security", ["add-generic-password", "-U", "-s", this.service, "-a", account, "-w", value], {
      stdio: ["ignore", "pipe", "pipe"],
    });
  }

  delete(account) {
    if (!this.isAvailable) return;
    try {
      execFileSync("security", ["delete-generic-password", "-s", this.service, "-a", account], {
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      const stderr = String(error.stderr || "").trim().toLowerCase();
      if (!stderr.includes("could not be found")) throw error;
    }
  }

  loadUpstreamSecrets() {
    return {
      apiId: this.get(UPSTREAM_API_ID_ACCOUNT) || "",
      apiHash: this.get(UPSTREAM_API_HASH_ACCOUNT) || "",
      phone: this.get(UPSTREAM_PHONE_ACCOUNT) || "",
      session: this.get(UPSTREAM_SESSION_ACCOUNT) || "",
    };
  }

  saveUpstreamCredentials({ apiId, apiHash, phone }) {
    this.set(UPSTREAM_API_ID_ACCOUNT, String(apiId));
    this.set(UPSTREAM_API_HASH_ACCOUNT, String(apiHash));
    if (String(phone || "").trim()) this.set(UPSTREAM_PHONE_ACCOUNT, String(phone).trim());
    else this.delete(UPSTREAM_PHONE_ACCOUNT);
  }

  saveUpstreamSession(session) {
    this.set(UPSTREAM_SESSION_ACCOUNT, session);
  }

  clearUpstreamSession() {
    this.delete(UPSTREAM_SESSION_ACCOUNT);
  }

  clearUpstreamCredentials() {
    this.delete(UPSTREAM_API_ID_ACCOUNT);
    this.delete(UPSTREAM_API_HASH_ACCOUNT);
    this.delete(UPSTREAM_PHONE_ACCOUNT);
  }

  loadMcpToken() {
    return this.get(MCP_TOKEN_ACCOUNT) || "";
  }

  saveMcpToken(token) {
    this.set(MCP_TOKEN_ACCOUNT, token);
  }

  loadOrCreateMcpToken({ envToken = "", legacyPath = "" } = {}) {
    const cleanEnvToken = String(envToken || "").trim();
    if (cleanEnvToken) {
      if (legacyPath) fs.rmSync(legacyPath, { force: true });
      return { token: cleanEnvToken, envManaged: true };
    }
    const existing = this.loadMcpToken();
    if (existing) {
      if (legacyPath) fs.rmSync(legacyPath, { force: true });
      return { token: existing, envManaged: false };
    }
    if (legacyPath && fs.existsSync(legacyPath)) {
      const migrated = fs.readFileSync(legacyPath, "utf8").trim();
      fs.rmSync(legacyPath, { force: true });
      if (migrated) {
        if (this.isAvailable) this.saveMcpToken(migrated);
        return { token: migrated, envManaged: false };
      }
    }
    const token = randomBytes(24).toString("base64url");
    if (this.isAvailable) this.saveMcpToken(token);
    return { token, envManaged: false };
  }

  rotateMcpToken() {
    if (!this.isAvailable) throw new Error("MCP token rotation requires macOS Keychain");
    const token = randomBytes(24).toString("base64url");
    this.saveMcpToken(token);
    return token;
  }
}

class NativeConfig {
  static create(secretStore = new NativeSecretStore()) {
    loadProjectEnv();
    const home = ensureDir(DEFAULT_CONFIG_HOME);
    const mcpSettingsPath = path.resolve(process.env.TP_MCP_SETTINGS || path.join(home, "mcp_settings.json"));
    const imessageSettingsPath = path.resolve(process.env.TP_IMESSAGE_SETTINGS || path.join(home, "imessage_settings.json"));
    const imessageVisibleChatsPath = path.resolve(process.env.TP_IMESSAGE_VISIBLE_CHATS || path.join(home, "imessage_visible_chats.json"));
    const mcpSaved = NativeConfig.loadJsonObject(mcpSettingsPath);
    const imessageSaved = NativeConfig.loadJsonObject(imessageSettingsPath);
    const savedSecrets = secretStore.isAvailable ? secretStore.loadUpstreamSecrets() : { apiId: "", apiHash: "", phone: "", session: "" };
    const { token, envManaged } = secretStore.loadOrCreateMcpToken({
      envToken: process.env.TP_MCP_TOKEN || "",
      legacyPath: path.join(home, "mcp_token"),
    });
    let mcpScheme = String(process.env.TP_MCP_SCHEME || mcpSaved.scheme || "http").trim().toLowerCase() || "http";
    if (!["http", "https"].includes(mcpScheme)) mcpScheme = "http";
    const config = new NativeConfig({
      dashboardHost: process.env.TP_DASHBOARD_HOST || "127.0.0.1",
      dashboardPort: Number(process.env.TP_DASHBOARD_PORT || "8788"),
      mcpHost: process.env.TP_MCP_HOST || mcpSaved.host || "127.0.0.1",
      mcpPort: Number(process.env.TP_MCP_PORT || mcpSaved.port || 8791),
      mcpScheme,
      mcpPath: process.env.TP_MCP_PATH || "/mcp",
      mcpToken: token,
      mcpTokenEnvManaged: envManaged,
      mcpTlsCertName: process.env.TP_MCP_TLS_CERT || "",
      mcpTlsKeyName: process.env.TP_MCP_TLS_KEY || "",
      upstreamApiId: Number(savedSecrets.apiId || process.env.TG_API_ID || 0),
      upstreamApiHash: savedSecrets.apiHash || process.env.TG_API_HASH || "",
      upstreamPhone: process.env.TG_PHONE || savedSecrets.phone || "",
      upstreamSessionString: process.env.TP_UPSTREAM_SESSION_STRING || savedSecrets.session || "",
      upstreamSessionName: process.env.TP_UPSTREAM_SESSION || path.join(home, "sessions", "proxy_upstream"),
      mcpSettingsName: mcpSettingsPath,
      cloudFolderName: process.env.TP_CLOUD_FOLDER || "Cloud",
      allowMemberListing: !["0", "false", "False"].includes(process.env.TP_ALLOW_MEMBER_LISTING || "1"),
      updateBufferSize: Number(process.env.TP_UPDATE_BUFFER_SIZE || 1000),
      upstreamReconnectMinDelay: Number(process.env.TP_UPSTREAM_RECONNECT_MIN_DELAY || 2),
      upstreamReconnectMaxDelay: Number(process.env.TP_UPSTREAM_RECONNECT_MAX_DELAY || 30),
      whatsappCloudLabelName: process.env.TP_WHATSAPP_CLOUD_LABEL || process.env.TP_CLOUD_FOLDER || "Cloud",
      whatsappAuthDir: process.env.TP_WHATSAPP_AUTH_DIR || path.join(home, "whatsapp-auth"),
      imessageEnabled: !["0", "false", "False"].includes(process.env.TP_IMESSAGE_ENABLED || (imessageSaved.enabled ? "1" : "0")),
      imessageMessagesAppAccessible: Boolean(imessageSaved.messages_app_accessible),
      imessageDatabaseAccessible: Boolean(imessageSaved.database_accessible),
      imessageDbName: process.env.TP_IMESSAGE_DB || DEFAULT_MESSAGES_DB,
      imessageSettingsName: imessageSettingsPath,
      imessageVisibleChatsName: imessageVisibleChatsPath,
    });
    if (
      !String(process.env.TP_MCP_SCHEME || "").trim()
      && config.mcpScheme === "https"
      && !config.mcpTlsConfigured()
    ) {
      config.mcpScheme = "http";
      config.saveMcpSettings();
    }
    return config;
  }

  static loadJsonObject(filePath) {
    try {
      const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  }

  constructor(fields) {
    Object.assign(this, fields);
  }

  get mcpEndpoint() {
    return `${this.mcpScheme}://${this.mcpHost}:${this.mcpPort}${this.mcpPath}`;
  }

  get mcpTlsCertPath() {
    return this.mcpTlsCertName ? path.resolve(this.mcpTlsCertName) : null;
  }

  get mcpTlsKeyPath() {
    return this.mcpTlsKeyName ? path.resolve(this.mcpTlsKeyName) : null;
  }

  get imessageDbPath() {
    return path.resolve(this.imessageDbName);
  }

  get imessageVisibleChatsPath() {
    ensureDir(path.dirname(this.imessageVisibleChatsName));
    return path.resolve(this.imessageVisibleChatsName);
  }

  saveMcpSettings() {
    ensureDir(path.dirname(this.mcpSettingsName));
    fs.writeFileSync(this.mcpSettingsName, `${JSON.stringify({
      host: this.mcpHost,
      port: this.mcpPort,
      scheme: this.mcpScheme,
    }, null, 2)}\n`, "utf8");
  }

  saveIMessageSettings() {
    ensureDir(path.dirname(this.imessageSettingsName));
    fs.writeFileSync(this.imessageSettingsName, `${JSON.stringify({
      enabled: this.imessageEnabled,
      messages_app_accessible: this.imessageMessagesAppAccessible,
      database_accessible: this.imessageDatabaseAccessible,
    }, null, 2)}\n`, "utf8");
  }

  mcpTlsConfigured() {
    return Boolean(this.mcpTlsCertPath && this.mcpTlsKeyPath && fs.existsSync(this.mcpTlsCertPath) && fs.existsSync(this.mcpTlsKeyPath));
  }

  validateMcpTlsConfig() {
    if (this.mcpScheme !== "https") return;
    if (!this.mcpTlsConfigured()) {
      throw new Error("HTTPS requires TP_MCP_TLS_CERT and TP_MCP_TLS_KEY to be set.");
    }
  }
}

class TelegramAuthManager {
  constructor(config, secretStore, telegramBridge) {
    this.config = config;
    this.secretStore = secretStore;
    this.telegramBridge = telegramBridge;
    this.pending = null;
    this.needsPassword = false;
    this.lastError = "";
  }

  async close() {
    if (this.pending?.client) {
      try {
        await this.pending.client.disconnect();
      } catch {}
    }
    this.pending = null;
    this.needsPassword = false;
  }

  getStatus() {
    const saved = this.secretStore.isAvailable ? this.secretStore.loadUpstreamSecrets() : null;
    const hasApiCredentials = Boolean(saved?.apiId && saved?.apiHash);
    const hasSession = Boolean(saved?.session);
    let nextStep = "credentials";
    if (this.needsPassword) nextStep = "password";
    else if (this.pending) nextStep = "code";
    else if (hasSession) nextStep = "ready";
    return {
      keychain_backend: this.secretStore.isAvailable ? "macOS Keychain" : "Unavailable",
      has_api_credentials: hasApiCredentials,
      has_session: hasSession,
      phone: saved?.phone || this.config.upstreamPhone,
      saved_phone: hasSession ? saved?.phone || null : null,
      next_step: nextStep,
      pending_phone: this.pending?.phone || null,
      last_error: this.lastError || null,
    };
  }

  async saveCredentials({ apiId, apiHash, phone }) {
    const apiIdText = String(apiId || "").trim();
    if (!apiIdText) throw new Error("Telegram API ID is required");
    const normalizedApiId = Number(apiIdText);
    if (!Number.isFinite(normalizedApiId)) throw new Error("Telegram API ID must be a number");
    const normalizedApiHash = String(apiHash || "").trim();
    if (!normalizedApiHash) throw new Error("Telegram API hash is required");
    const normalizedPhone = String(phone || "").trim();
    const previous = this.secretStore.isAvailable ? this.secretStore.loadUpstreamSecrets() : null;
    this.secretStore.saveUpstreamCredentials({ apiId: String(normalizedApiId), apiHash: normalizedApiHash, phone: normalizedPhone });
    if (previous && (previous.apiId !== String(normalizedApiId) || previous.apiHash !== normalizedApiHash)) {
      this.secretStore.clearUpstreamSession();
    }
    this.config.upstreamApiId = normalizedApiId;
    this.config.upstreamApiHash = normalizedApiHash;
    this.config.upstreamPhone = normalizedPhone;
    this.config.upstreamSessionString = "";
    this.lastError = "";
    await this.close();
    await this.telegramBridge.stop();
    return this.getStatus();
  }

  async requestCode({ phone = "" } = {}) {
    await this.close();
    const saved = this.secretStore.isAvailable ? this.secretStore.loadUpstreamSecrets() : null;
    const apiId = Number(saved?.apiId || this.config.upstreamApiId);
    const apiHash = saved?.apiHash || this.config.upstreamApiHash;
    const resolvedPhone = String(phone || "").trim() || saved?.phone || this.config.upstreamPhone;
    if (!apiId || !apiHash) throw new Error("Save your Telegram API ID and API hash first");
    if (!resolvedPhone) throw new Error("Telegram phone number is required before requesting a login code");
    const client = new TelegramClient(new StringSession(""), apiId, apiHash, { connectionRetries: 1 });
    await client.connect();
    const sent = await client.sendCode({ apiId, apiHash }, resolvedPhone);
    this.pending = { client, apiId, apiHash, phone: resolvedPhone, phoneCodeHash: sent.phoneCodeHash };
    this.needsPassword = false;
    this.lastError = "";
    this.secretStore.saveUpstreamCredentials({ apiId: String(apiId), apiHash, phone: resolvedPhone });
    this.config.upstreamPhone = resolvedPhone;
    return this.getStatus();
  }

  async clearSavedAuth() {
    await this.close();
    this.secretStore.clearUpstreamSession();
    this.secretStore.clearUpstreamCredentials();
    this.config.upstreamSessionString = "";
    this.config.upstreamApiId = 0;
    this.config.upstreamApiHash = "";
    this.config.upstreamPhone = "";
    this.lastError = "";
    await this.telegramBridge.stop();
    return this.getStatus();
  }

  async clearSavedSession() {
    await this.close();
    this.secretStore.clearUpstreamSession();
    this.config.upstreamSessionString = "";
    this.lastError = "";
    await this.telegramBridge.stop();
    return this.getStatus();
  }

  async submitCode({ code }) {
    if (!this.pending) throw new Error("Request a Telegram login code first");
    try {
      await this.pending.client.invoke(new Api.auth.SignIn({
        phoneNumber: this.pending.phone,
        phoneCodeHash: this.pending.phoneCodeHash,
        phoneCode: String(code || "").trim(),
      }));
    } catch (error) {
      if (String(error?.errorMessage || error?.message || "").includes("SESSION_PASSWORD_NEEDED")) {
        this.needsPassword = true;
        this.lastError = "";
        return this.getStatus();
      }
      throw error;
    }
    return this.#completeLogin();
  }

  async submitPassword({ password }) {
    if (!this.pending) throw new Error("Request a Telegram login code first");
    await this.pending.client.signInWithPassword(
      { apiId: this.pending.apiId, apiHash: this.pending.apiHash },
      {
        password: async () => String(password || ""),
        onError: async (error) => {
          this.lastError = error?.message || String(error);
          return true;
        },
      },
    );
    return this.#completeLogin();
  }

  async #completeLogin() {
    const pending = this.pending;
    if (!pending) throw new Error("Request a Telegram login code first");
    const me = await pending.client.getMe();
    const sessionString = pending.client.session.save();
    const phone = me?.phone || pending.phone;
    this.secretStore.saveUpstreamCredentials({ apiId: String(pending.apiId), apiHash: pending.apiHash, phone });
    this.secretStore.saveUpstreamSession(sessionString);
    this.config.upstreamApiId = pending.apiId;
    this.config.upstreamApiHash = pending.apiHash;
    this.config.upstreamPhone = phone;
    this.config.upstreamSessionString = sessionString;
    await pending.client.disconnect();
    this.pending = null;
    this.needsPassword = false;
    this.lastError = "";
    await this.telegramBridge.stop();
    const status = this.getStatus();
    status.account = {
      id: Number(me?.id || 0) || null,
      name: [me?.firstName, me?.lastName].filter(Boolean).join(" ").trim() || me?.username || "Telegram account",
      username: me?.username || null,
      phone,
    };
    status.next_step = "ready";
    return status;
  }
}

class NativeIMessageBridge {
  constructor({ dbPath, visibleChatsPath }) {
    this.dbPath = dbPath;
    this.visibleChatsPath = visibleChatsPath;
    ensureDir(path.dirname(visibleChatsPath));
    this.visibleChatIds = this.#loadVisibleChatIds();
  }

  async start() {}
  async stop() {}
  async close() {}

  async getStatus(limit = 200) {
    const [accounts, accountError] = await this.#safeAccounts();
    const [allChats, chatError, dbAccessible, dbError] = await this.#safeChatCollection(limit);
    const visibleChats = this.#filterVisibleChats(allChats);
    const connected = accounts.some((account) => String(account.connection || "").toLowerCase() === "connected");
    const lastError = accountError || dbError || chatError;
    return {
      ok: true,
      available: Boolean(accounts.length || allChats.length || !lastError),
      connected,
      has_session: Boolean(accounts.length),
      messages_app_accessible: !accountError || !chatError,
      database_accessible: dbAccessible,
      messages_app_error: accountError || chatError,
      database_error: dbError,
      automation_hint: "Grant Automation access to Messages and Full Disk Access for history reads if macOS prompts.",
      db_path: this.dbPath,
      accounts,
      all_chats: allChats,
      visible_chats: visibleChats,
      visible_chat_ids: visibleChats.map((chat) => chat.chat_id),
      chats: visibleChats,
      last_error: lastError,
    };
  }

  async getChat(chatId, limit = 50, visibleOnly = true) {
    const [chats] = await this.#safeChatCollection(1000);
    const scope = visibleOnly ? this.#filterVisibleChats(chats) : chats;
    const chat = scope.find((item) => item.chat_id === String(chatId || "").trim());
    if (!chat) {
      throw new Error(visibleOnly ? `iMessage chat is not visible through MCP: ${chatId}` : `Unknown iMessage chat: ${chatId}`);
    }
    return { ok: true, chat, messages: await this.#queryChatMessages(chat.chat_id, limit) };
  }

  async getLocalChat(chatId, limit = 50) {
    return this.getChat(chatId, limit, false);
  }

  async getUpdates(limit = 50) {
    const messages = (await this.#queryRecentMessages(Math.max(limit * 4, 200))).filter((message) => this.#isVisibleChatId(message.chat_id)).slice(-limit);
    return {
      ok: true,
      updates: messages.map((message) => ({
        kind: "new_message",
        chat_id: message.chat_id,
        message_id: message.id,
        message,
      })),
    };
  }

  async sendMessage(chatId, text) {
    const normalizedChatId = String(chatId || "").trim();
    const messageText = String(text || "").trim();
    if (!normalizedChatId) throw new Error("iMessage chat_id is required");
    if (!messageText) throw new Error("Message text is required");
    if (!this.#isVisibleChatId(normalizedChatId)) throw new Error(`iMessage chat is not visible through MCP: ${normalizedChatId}`);
    await this.#runScript(`
tell application "Messages"
  repeat with c in every chat
    try
      if (id of c as text) is ${JSON.stringify(normalizedChatId)} then
        send ${JSON.stringify(messageText)} to c
        return (id of c as text)
      end if
    end try
  end repeat
end tell
error "iMessage chat not found"
`);
    return {
      ok: true,
      message: {
        id: null,
        chat_id: normalizedChatId,
        sender: null,
        text: messageText,
        date: nowIso(),
        from_me: true,
        kind: "text",
      },
    };
  }

  async setChatVisibility(chatId, visible) {
    const normalizedChatId = String(chatId || "").trim();
    if (!normalizedChatId) throw new Error("iMessage chat_id is required");
    const [chats, chatError] = await this.#safeChatCollection(5000);
    if (!chats.some((chat) => chat.chat_id === normalizedChatId)) {
      if (chatError && !chats.length) throw new Error(chatError);
      throw new Error(`Unknown iMessage chat: ${normalizedChatId}`);
    }
    const updated = new Set(this.visibleChatIds);
    if (visible) updated.add(normalizedChatId);
    else updated.delete(normalizedChatId);
    this.visibleChatIds = updated;
    this.#saveVisibleChatIds();
    const visibleChats = this.#filterVisibleChats(chats);
    return {
      ok: true,
      chat_id: normalizedChatId,
      visible,
      visible_chat_ids: visibleChats.map((chat) => chat.chat_id),
      visible_chats: visibleChats,
      all_chats: chats,
      chats: visibleChats,
    };
  }

  async #safeAccounts() {
    try {
      return [await this.#listAccounts(), null];
    } catch (error) {
      return [[], error.message || String(error)];
    }
  }

  async #safeChatCollection(limit) {
    let scriptChats = {};
    let chatError = null;
    try {
      scriptChats = Object.fromEntries((await this.#listScriptableChats()).map((chat) => [chat.chat_id, chat]));
    } catch (error) {
      chatError = error.message || String(error);
    }
    let dbSummaries = {};
    let dbError = null;
    let dbAccessible = true;
    try {
      dbSummaries = Object.fromEntries((await this.#queryChatSummaries(Math.max(limit, 500))).map((chat) => [chat.chat_id, chat]));
    } catch (error) {
      dbAccessible = false;
      dbError = error.message || String(error);
    }
    return [this.#mergeChats(scriptChats, dbSummaries).slice(0, limit), chatError, dbAccessible, dbError];
  }

  #filterVisibleChats(chats) {
    return chats.filter((chat) => this.#isVisibleChatId(chat.chat_id));
  }

  #isVisibleChatId(chatId) {
    const normalized = String(chatId || "").trim();
    return Boolean(normalized) && this.visibleChatIds.has(normalized);
  }

  #loadVisibleChatIds() {
    try {
      const payload = JSON.parse(fs.readFileSync(this.visibleChatsPath, "utf8"));
      return new Set(Array.isArray(payload?.chat_ids) ? payload.chat_ids.map((value) => String(value).trim()).filter(Boolean) : []);
    } catch {
      return new Set();
    }
  }

  #saveVisibleChatIds() {
    fs.writeFileSync(this.visibleChatsPath, `${JSON.stringify({ chat_ids: [...this.visibleChatIds].sort() }, null, 2)}\n`, "utf8");
  }

  async #listAccounts() {
    const rows = await this.#runScript(ACCOUNT_STATUS_SCRIPT);
    return rows.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
      const parts = line.split(FIELD_SEPARATOR);
      while (parts.length < 5) parts.push("");
      const [accountId, connection, enabled, description, serviceType] = parts;
      return {
        id: cleanScriptValue(accountId),
        connection: cleanScriptValue(connection),
        enabled: truthy(enabled),
        description: cleanScriptValue(description),
        service_type: cleanScriptValue(serviceType) || "Messages",
      };
    });
  }

  async #listScriptableChats() {
    const rows = await this.#runScript(CHAT_LIST_SCRIPT);
    return rows.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
      const parts = line.split(FIELD_SEPARATOR);
      while (parts.length < 7) parts.push("");
      const [chatId, title, participantsText, participantCount, accountId, scriptOrder, serviceType] = parts;
      const participants = participantsText.split(LIST_SEPARATOR).map(cleanScriptValue).filter(Boolean);
      const count = Number(participantCount || participants.length || 0);
      return {
        chat_id: cleanScriptValue(chatId),
        title: cleanScriptValue(title),
        participants,
        participant_count: count,
        kind: count > 1 ? "group" : "dm",
        account_id: cleanScriptValue(accountId) || null,
        script_order: Number(scriptOrder || 0) || null,
        service_type: cleanScriptValue(serviceType) || "Messages",
      };
    });
  }

  async #queryChatSummaries(limit) {
    const rows = await this.#runSql(`
      SELECT
        c.guid AS chat_id,
        COALESCE(NULLIF(c.display_name, ''), NULLIF(c.chat_identifier, ''), NULLIF(c.room_name, ''), c.guid) AS title,
        COUNT(DISTINCT chj.handle_id) AS participant_count,
        GROUP_CONCAT(DISTINCT COALESCE(h.id, h.uncanonicalized_id)) AS participants,
        COALESCE(NULLIF(c.service_name, ''), 'Messages') AS service_type,
        MAX(m.date) AS last_message_date,
        (
          SELECT COALESCE(NULLIF(m2.text, ''), NULLIF(hex(m2.attributedBody), ''))
          FROM message m2
          JOIN chat_message_join cmj2 ON cmj2.message_id = m2.ROWID
          WHERE cmj2.chat_id = c.ROWID
          ORDER BY m2.date DESC
          LIMIT 1
        ) AS last_message_text,
        COALESCE(SUM(CASE WHEN m.is_from_me = 0 AND COALESCE(m.is_read, 1) = 0 THEN 1 ELSE 0 END), 0) AS unread_count
      FROM chat c
      LEFT JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
      LEFT JOIN handle h ON h.ROWID = chj.handle_id
      LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
      LEFT JOIN message m ON m.ROWID = cmj.message_id
      GROUP BY c.ROWID
      ORDER BY COALESCE(MAX(m.date), 0) DESC
      LIMIT ${Number(limit)}
    `);
    return rows.map((row) => {
      const participantHandles = String(row.participants || "").split(",").filter(Boolean);
      const lastText = this.#normalizeMessageText(row.last_message_text, null, []);
      return {
        chat_id: row.chat_id,
        title: row.title,
        participants: participantHandles,
        participant_count: Number(row.participant_count || participantHandles.length || 0),
        kind: Number(row.participant_count || 0) > 1 ? "group" : "dm",
        service_type: row.service_type,
        last_message_at: appleMessageDateToIso(row.last_message_date),
        last_message_text: lastText,
        unread_count: Number(row.unread_count || 0),
      };
    });
  }

  async #queryChatMessages(chatId, limit) {
    const rows = await this.#runSql(`
      SELECT
        m.ROWID AS message_id,
        c.guid AS chat_id,
        COALESCE(NULLIF(c.service_name, ''), 'Messages') AS service_type,
        COALESCE(h.id, h.uncanonicalized_id) AS sender,
        m.text AS text,
        hex(m.attributedBody) AS attributed_body_hex,
        m.date AS message_date,
        m.is_from_me AS is_from_me,
        m.subject AS subject,
        m.cache_has_attachments AS has_attachments
      FROM message m
      JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
      JOIN chat c ON c.ROWID = cmj.chat_id
      LEFT JOIN handle h ON h.ROWID = m.handle_id
      WHERE c.guid = ${sqlQuote(chatId)}
      ORDER BY m.date DESC
      LIMIT ${Number(limit)}
    `);
    const messages = [];
    for (const row of rows) {
      messages.push(await this.#serializeDbMessage(row));
    }
    return messages.reverse();
  }

  async #queryRecentMessages(limit) {
    const rows = await this.#runSql(`
      SELECT
        m.ROWID AS message_id,
        c.guid AS chat_id,
        COALESCE(NULLIF(c.service_name, ''), 'Messages') AS service_type,
        COALESCE(h.id, h.uncanonicalized_id) AS sender,
        m.text AS text,
        hex(m.attributedBody) AS attributed_body_hex,
        m.date AS message_date,
        m.is_from_me AS is_from_me,
        m.subject AS subject,
        m.cache_has_attachments AS has_attachments
      FROM message m
      JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
      JOIN chat c ON c.ROWID = cmj.chat_id
      LEFT JOIN handle h ON h.ROWID = m.handle_id
      ORDER BY m.date DESC
      LIMIT ${Number(limit)}
    `);
    const messages = [];
    for (const row of rows) {
      messages.push(await this.#serializeDbMessage(row));
    }
    return messages.reverse();
  }

  async #serializeDbMessage(row) {
    const attachments = Number(row.has_attachments || 0) ? await this.#attachmentPaths(Number(row.message_id)) : [];
    return {
      id: String(row.message_id),
      chat_id: row.chat_id,
      sender: row.sender,
      text: this.#normalizeMessageText(row.text, row.attributed_body_hex, attachments, row.subject),
      date: appleMessageDateToIso(row.message_date),
      from_me: Boolean(row.is_from_me),
      kind: "text",
      service_type: row.service_type,
      attachments,
    };
  }

  async #attachmentPaths(messageId) {
    const rows = await this.#runSql(`
      SELECT attachment.filename
      FROM attachment
      JOIN message_attachment_join ON attachment.ROWID = message_attachment_join.attachment_id
      WHERE message_attachment_join.message_id = ${Number(messageId)}
    `);
    return rows.map((row) => row.filename).filter(Boolean).map(String);
  }

  #normalizeMessageText(text, attributedBodyHex, attachments, subject = null) {
    let plainText = text != null && text !== "" ? String(text).trim() : "";
    let attributedHex = attributedBodyHex != null && attributedBodyHex !== "" ? String(attributedBodyHex).trim() : "";
    if (plainText && !attributedHex && plainText.length >= 32 && plainText.length % 2 === 0 && /^[0-9A-Fa-f]+$/.test(plainText)) {
      attributedHex = plainText;
      plainText = "";
    }
    let url = null;
    if (!plainText && attributedHex) {
      const decoded = decodeAttributedBody(attributedHex);
      plainText = decoded.text || "";
      url = decoded.url || null;
    }
    if (!plainText) plainText = "[No text content]";
    if (subject != null && subject !== "") plainText = `Subject: ${subject}\n${plainText}`;
    if (attachments.length) plainText += `\n[Attachments: ${attachments.length}]`;
    if (url) plainText += `\n[URL: ${url}]`;
    return plainText;
  }

  #mergeChats(scriptChats, dbSummaries) {
    const merged = [];
    const allChatIds = new Set([...Object.keys(scriptChats), ...Object.keys(dbSummaries)]);
    for (const chatId of allChatIds) {
      const scriptChat = scriptChats[chatId] || {};
      const dbChat = dbSummaries[chatId] || {};
      const participants = scriptChat.participants || dbChat.participants || [];
      const title = scriptChat.title || dbChat.title || (participants.length === 1 ? participants[0] : participants.slice(0, 3).join(", ")) || chatId;
      const participantCount = Number(scriptChat.participant_count || dbChat.participant_count || participants.length || 0);
      const kind = dbChat.kind || (participantCount > 1 ? "group" : "dm");
      merged.push({
        chat_id: chatId,
        title,
        kind,
        service_type: scriptChat.service_type || dbChat.service_type || "Messages",
        participants,
        participant_count: participantCount,
        last_message_at: dbChat.last_message_at || null,
        last_message_text: dbChat.last_message_text || null,
        unread_count: Number(dbChat.unread_count || 0),
        account_id: scriptChat.account_id || dbChat.account_id || null,
        script_order: scriptChat.script_order ?? null,
      });
    }
    merged.sort((a, b) => String(a.title || "").localeCompare(String(b.title || "")));
    merged.sort((a, b) => (a.script_order ?? 1e9) - (b.script_order ?? 1e9));
    merged.sort((a, b) => String(b.last_message_at || "").localeCompare(String(a.last_message_at || "")));
    return merged;
  }

  async #runSql(sql) {
    try {
      const { stdout } = await execFileAsync("sqlite3", ["-readonly", "-json", this.dbPath, sql], {
        encoding: "utf8",
        maxBuffer: 10 * 1024 * 1024,
      });
      const parsed = stdout.trim() ? JSON.parse(stdout) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      const message = String(error.stderr || error.stdout || error.message || error);
      if (message.toLowerCase().includes("authorization denied") || message.toLowerCase().includes("unable to open database file")) {
        throw new Error("Messages history is blocked by macOS privacy. Grant Full Disk Access to the app or terminal that runs this proxy to read chat history from chat.db.");
      }
      throw new Error(message.trim() || "sqlite3 query failed");
    }
  }

  async #runScript(script) {
    try {
      const { stdout } = await execFileAsync("osascript", ["-e", script], {
        encoding: "utf8",
        timeout: 15000,
        maxBuffer: 5 * 1024 * 1024,
      });
      return stdout.trim();
    } catch (error) {
      throw new Error(String(error.stderr || error.stdout || error.message || error).trim() || "Messages automation failed");
    }
  }
}

class NativeMcpServer {
  constructor(config, backend) {
    this.config = config;
    this.backend = backend;
    this.server = null;
    this.sessions = new Map();
  }

  get isRunning() {
    return Boolean(this.server?.listening);
  }

  async start() {
    if (this.server) return;
    this.config.validateMcpTlsConfig();
    const listener = this.config.mcpScheme === "https"
      ? https.createServer({
          cert: fs.readFileSync(this.config.mcpTlsCertPath),
          key: fs.readFileSync(this.config.mcpTlsKeyPath),
        }, (req, res) => void this.#handle(req, res))
      : http.createServer((req, res) => void this.#handle(req, res));
    await new Promise((resolve, reject) => {
      listener.once("error", reject);
      listener.listen(this.config.mcpPort, this.config.mcpHost, resolve);
    });
    this.server = listener;
    this.config.mcpPort = listener.address().port;
  }

  async stop() {
    if (!this.server) return;
    for (const session of this.sessions.values()) {
      try {
        session.res.end();
      } catch {}
    }
    this.sessions.clear();
    await new Promise((resolve) => this.server.close(resolve));
    this.server = null;
  }

  async #handle(req, res) {
    try {
      const url = new URL(req.url || "/", `http://${this.config.mcpHost}:${this.config.mcpPort}`);
      if (url.pathname !== this.config.mcpPath) {
        this.#writeJson(res, 404, { error: "Not Found" });
        return;
      }
      this.#validateAuth(req);
      if (req.method === "GET") {
        const accept = String(req.headers.accept || "");
        if (accept.includes("text/event-stream")) {
          const sessionId = String(req.headers["mcp-session-id"] || "");
          const session = this.sessions.get(sessionId);
          if (!session) {
            this.#writeJson(res, 400, { error: "Unknown MCP session" });
            return;
          }
          res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            Connection: "keep-alive",
          });
          res.write(": connected\n\n");
          session.res = res;
          req.on("close", () => {
            if (this.sessions.get(sessionId)?.res === res) {
              session.res = null;
            }
          });
          return;
        }
        this.#writeJson(res, 200, {
          transport: "http+sse",
          name: "telethon-proxy-mcp",
          mcp_path: this.config.mcpPath,
        });
        return;
      }
      if (req.method === "DELETE") {
        const sessionId = String(req.headers["mcp-session-id"] || "");
        this.sessions.delete(sessionId);
        this.#writeJson(res, 200, { ok: true });
        return;
      }
      if (req.method !== "POST") {
        this.#writeJson(res, 405, { error: "Method Not Allowed" });
        return;
      }
      const body = await this.#readJson(req);
      const method = body.method;
      if (method === "notifications/initialized") {
        this.#writeJson(res, 202, { ok: true });
        return;
      }
      let sessionId = String(req.headers["mcp-session-id"] || "");
      if (method === "initialize") {
        sessionId = randomBytes(16).toString("hex");
        this.sessions.set(sessionId, { id: sessionId, subscriptions: new Set(), res: null });
        const response = {
          jsonrpc: "2.0",
          id: body.id ?? null,
          result: {
            protocolVersion: "2025-06-18",
            capabilities: {
              tools: { listChanged: false },
              resources: { listChanged: false, subscribe: true },
            },
            serverInfo: { name: "telethon-proxy-mcp", version: "0.2.0" },
            instructions: "Cloud-scoped Telegram access, WhatsApp chats carrying the Cloud label, and local Messages chats allowed through the desktop app.",
          },
        };
        this.#writeJson(res, 200, response, { "Mcp-Session-Id": sessionId });
        return;
      }
      const session = this.sessions.get(sessionId);
      if (!session) {
        this.#writeJson(res, 400, { error: "Missing or invalid MCP session" });
        return;
      }
      const rpc = await this.#handleRpc(body, session);
      this.#writeJson(res, 200, rpc);
    } catch (error) {
      this.#writeJson(res, error?.status || 500, { error: error.message || String(error) });
    }
  }

  async #handleRpc(request, session) {
    const id = request.id ?? null;
    const method = request.method;
    const params = request.params && typeof request.params === "object" ? request.params : {};
    try {
      if (method === "ping") return { jsonrpc: "2.0", id, result: {} };
      if (method === "tools/list") return { jsonrpc: "2.0", id, result: { tools: this.backend.mcpTools() } };
      if (method === "tools/call") return { jsonrpc: "2.0", id, result: await this.backend.mcpCallTool(params) };
      if (method === "resources/list") return { jsonrpc: "2.0", id, result: { resources: await this.backend.mcpResources() } };
      if (method === "resources/read") return { jsonrpc: "2.0", id, result: await this.backend.mcpReadResource(params) };
      if (method === "resources/subscribe") {
        session.subscriptions.add(String(params.uri || ""));
        return { jsonrpc: "2.0", id, result: {} };
      }
      if (method === "resources/unsubscribe") {
        session.subscriptions.delete(String(params.uri || ""));
        return { jsonrpc: "2.0", id, result: {} };
      }
      return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } };
    } catch (error) {
      return { jsonrpc: "2.0", id, error: { code: -32000, message: error.message || String(error) } };
    }
  }

  #validateAuth(req) {
    const expected = `Bearer ${this.config.mcpToken}`;
    if (String(req.headers.authorization || "") !== expected) {
      const error = new Error("Unauthorized");
      error.status = 401;
      throw error;
    }
  }

  async #readJson(req) {
    const chunks = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    const raw = Buffer.concat(chunks).toString("utf8");
    return raw ? JSON.parse(raw) : {};
  }

  #writeJson(res, status, payload, extraHeaders = {}) {
    const body = Buffer.from(JSON.stringify(payload));
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": String(body.length),
      "Cache-Control": "no-store",
      ...extraHeaders,
    });
    res.end(body);
  }
}

export class NativeAppBackend {
  constructor() {
    this.secretStore = new NativeSecretStore();
    this.config = NativeConfig.create(this.secretStore);
    this.telegramBridge = new GramJsTelegramBridge({ folderName: this.config.cloudFolderName });
    this.telegramAuth = new TelegramAuthManager(this.config, this.secretStore, this.telegramBridge);
    this.whatsapp = new WhatsAppBridgeService({
      authDir: this.config.whatsappAuthDir,
      listen: false,
    });
    this.imessage = new NativeIMessageBridge({
      dbPath: this.config.imessageDbPath,
      visibleChatsPath: this.config.imessageVisibleChatsPath,
    });
    this.mcp = new NativeMcpServer(this.config, this);
  }

  async start() {
    try {
      await this.whatsapp.start();
    } catch {
      // WhatsApp bridge can start lazily; keep the app available.
    }
    await this.imessage.start();
    try {
      await this.mcp.start();
    } catch {
      // If the MCP port is busy we still want the app UI to come up.
    }
  }

  async stop() {
    await this.telegramAuth.close();
    await this.telegramBridge.stop();
    await this.mcp.stop();
    await this.whatsapp.stop();
    await this.imessage.stop();
  }

  async getCoreOverview() {
    const whatsapp = await this.getWhatsAppAuth();
    const imessage = await this.getIMessageAuth();
    return {
      generated_at: nowIso(),
      error: null,
      config: {
        cloud_folder_name: this.config.cloudFolderName,
        whatsapp_cloud_label_name: this.config.whatsappCloudLabelName,
        dashboard_host: this.config.dashboardHost,
        dashboard_port: this.config.dashboardPort,
        allow_member_listing: this.config.allowMemberListing,
        imessage_enabled: this.config.imessageEnabled,
        upstream_reconnect_min_delay: this.config.upstreamReconnectMinDelay,
        upstream_reconnect_max_delay: this.config.upstreamReconnectMaxDelay,
        imessage_db_path: this.config.imessageDbPath,
      },
      upstream: { name: "Unavailable", phone: null, username: null },
      mcp: {
        scheme: this.config.mcpScheme,
        host: this.config.mcpHost,
        port: this.config.mcpPort,
        path: this.config.mcpPath,
        endpoint: this.config.mcpEndpoint,
        listening: this.mcp.isRunning,
        transport: `${this.config.mcpScheme.toUpperCase()} JSON-RPC`,
        auth: "Authorization: Bearer <token>",
        allowed_origin: "localhost / 127.0.0.1",
        token_hidden: true,
        token_env_managed: this.config.mcpTokenEnvManaged,
        tls_configured: this.config.mcpTlsConfigured(),
        bind_options: await this.getMcpBindOptions(),
      },
      telegram_auth: this.getTelegramAuth(),
      whatsapp,
      imessage,
      chats: [],
    };
  }

  async getOverview() {
    const payload = await this.getCoreOverview();
    try {
      payload.chats = await this.telegramBridge.getOverviewChats();
      payload.upstream = await this.telegramBridge.getIdentity();
      payload.error = null;
    } catch (error) {
      payload.chats = [];
      payload.error = "Upstream Telegram connection is not available yet. Use Telegram -> Settings to authorize.";
    }
    return payload;
  }

  async getTelegramChat(peerId, limit = 50) {
    return this.telegramBridge.getChat(peerId, limit);
  }

  getTelegramAuth() {
    return this.telegramAuth.getStatus();
  }

  async getWhatsAppAuth() {
    try {
      const payload = await this.whatsapp.authStatus();
      payload.available = true;
      return payload;
    } catch (error) {
      return {
        ok: false,
        available: false,
        connected: false,
        has_session: false,
        cloud_label_name: this.config.whatsappCloudLabelName,
        chats: [],
        last_error: error.message || String(error),
      };
    }
  }

  async getWhatsAppChat(jid) {
    try {
      await this.whatsapp.ensureChatHistory(jid, 80);
      const chats = (await this.whatsapp.authStatus()).chats || [];
      return {
        ok: true,
        chat: chats.find((chat) => chat.jid === jid) || null,
        messages: this.whatsapp._chatMessages(jid, 80),
      };
    } catch (error) {
      return { chat: null, messages: [], error: error.message || String(error) };
    }
  }

  async getIMessageAuth() {
    if (!this.config.imessageEnabled) {
      return {
        ok: true,
        enabled: false,
        available: false,
        connected: false,
        has_session: false,
        messages_app_accessible: this.config.imessageMessagesAppAccessible,
        database_accessible: this.config.imessageDatabaseAccessible,
        messages_app_error: null,
        database_error: null,
        automation_hint: "Enabling Messages will prompt macOS for Messages control access and may require Full Disk Access for chat history.",
        db_path: this.config.imessageDbPath,
        accounts: [],
        all_chats: [],
        visible_chats: [],
        visible_chat_ids: [],
        chats: [],
        last_error: null,
      };
    }
    const payload = await this.imessage.getStatus(500);
    this.config.imessageMessagesAppAccessible = Boolean(payload.messages_app_accessible);
    this.config.imessageDatabaseAccessible = Boolean(payload.database_accessible);
    this.config.saveIMessageSettings();
    payload.enabled = true;
    return payload;
  }

  async getIMessageChat(chatId) {
    try {
      return await this.imessage.getChat(chatId, 80, true);
    } catch (error) {
      return { chat: null, messages: [], error: error.message || String(error) };
    }
  }

  async setIMessageVisibility({ chatId, visible }) {
    if (!this.config.imessageEnabled) throw new Error("Messages integration is disabled");
    await this.imessage.setChatVisibility(chatId, visible);
    return this.getIMessageAuth();
  }

  async setIMessageEnabled(enabled) {
    this.config.imessageEnabled = Boolean(enabled);
    this.config.saveIMessageSettings();
    const result = await this.getIMessageAuth();
    result.message = enabled ? "Messages integration enabled." : "Messages integration disabled.";
    return result;
  }

  getMcpToken() {
    return { token: this.config.mcpToken, env_managed: this.config.mcpTokenEnvManaged };
  }

  rotateMcpToken() {
    if (this.config.mcpTokenEnvManaged) {
      throw new Error("MCP token is managed by TP_MCP_TOKEN and cannot be rotated from the UI");
    }
    const token = this.secretStore.rotateMcpToken();
    this.config.mcpToken = token;
    return { token, env_managed: false, message: "MCP bearer token rotated." };
  }

  async setMcpConfig({ host, port, scheme }) {
    const normalizedHost = String(host || "").trim();
    if (!normalizedHost) throw new Error("MCP host is required");
    const normalizedScheme = String(scheme || this.config.mcpScheme || "http").trim().toLowerCase();
    if (!["http", "https"].includes(normalizedScheme)) throw new Error("MCP protocol must be http or https");
    const normalizedPort = Number(port);
    if (!Number.isFinite(normalizedPort)) throw new Error("MCP port must be a number");
    if (normalizedPort < 1 || normalizedPort > 65535) throw new Error("MCP port must be between 1 and 65535");
    const previous = { scheme: this.config.mcpScheme, host: this.config.mcpHost, port: this.config.mcpPort };
    if (normalizedHost === previous.host && normalizedPort === previous.port && normalizedScheme === previous.scheme && this.mcp.isRunning) {
      return {
        ok: true,
        scheme: this.config.mcpScheme,
        host: this.config.mcpHost,
        port: this.config.mcpPort,
        path: this.config.mcpPath,
        endpoint: this.config.mcpEndpoint,
        listening: this.mcp.isRunning,
        message: "MCP listener already matches the requested protocol, interface, and port.",
      };
    }
    if (normalizedScheme === "https" && !this.config.mcpTlsConfigured()) {
      throw new Error("HTTPS requires TP_MCP_TLS_CERT and TP_MCP_TLS_KEY to be set.");
    }
    const wasRunning = this.mcp.isRunning;
    try {
      if (wasRunning) await this.mcp.stop();
      this.config.mcpScheme = normalizedScheme;
      this.config.mcpHost = normalizedHost;
      this.config.mcpPort = normalizedPort;
      this.config.saveMcpSettings();
      await this.mcp.start();
    } catch (error) {
      this.config.mcpScheme = previous.scheme;
      this.config.mcpHost = previous.host;
      this.config.mcpPort = previous.port;
      this.config.saveMcpSettings();
      try {
        if (wasRunning) await this.mcp.start();
      } catch {}
      throw error;
    }
    return {
      ok: true,
      scheme: this.config.mcpScheme,
      host: this.config.mcpHost,
      port: this.config.mcpPort,
      path: this.config.mcpPath,
      endpoint: this.config.mcpEndpoint,
      listening: this.mcp.isRunning,
      message: `MCP listener moved to ${this.config.mcpEndpoint}.`,
    };
  }

  async getMcpBindOptions() {
    const options = [];
    const seen = new Set();
    const add = (host, label, iface = "") => {
      host = String(host || "").trim();
      if (!host || seen.has(host)) return;
      seen.add(host);
      const payload = { host, label };
      if (iface) payload.interface = iface;
      options.push(payload);
    };
    add("127.0.0.1", "Localhost (lo0)", "lo0");
    add("0.0.0.0", "All interfaces", "*");
    const interfaces = os.networkInterfaces();
    for (const [name, entries] of Object.entries(interfaces)) {
      for (const entry of entries || []) {
        if (entry.family !== "IPv4" || entry.internal || !entry.address) continue;
        const label = name.startsWith("utun") || entry.address.startsWith("100.")
          ? `Tailscale or VPN (${name})`
          : name.startsWith("en")
            ? `Network interface (${name})`
            : `Interface ${name}`;
        add(entry.address, `${label} (${entry.address})`, name);
      }
    }
    add(this.config.mcpHost, `Current MCP host (${this.config.mcpHost})`);
    return options;
  }

  async mcpTools() {
    return [
      { name: "telegram.list_chats", inputSchema: { type: "object", properties: { limit: { type: "integer" } } } },
      { name: "telegram.get_messages", inputSchema: { type: "object", properties: { peer: {}, limit: { type: "integer" } }, required: ["peer"] } },
      { name: "telegram.search_messages", inputSchema: { type: "object", properties: { query: { type: "string" }, peer: {}, limit: { type: "integer" } }, required: ["query"] } },
      { name: "telegram.send_message", inputSchema: { type: "object", properties: { peer: {}, text: { type: "string" }, reply_to_message_id: { type: "integer" } }, required: ["peer", "text"] } },
      { name: "telegram.delete_messages", inputSchema: { type: "object", properties: { peer: {}, message_ids: { type: "array", items: { type: "integer" } } }, required: ["peer", "message_ids"] } },
      { name: "telegram.mark_read", inputSchema: { type: "object", properties: { peer: {}, max_id: { type: "integer" } }, required: ["peer"] } },
      { name: "telegram.list_members", inputSchema: { type: "object", properties: { peer: {}, limit: { type: "integer" } }, required: ["peer"] } },
      { name: "telegram.get_updates", inputSchema: { type: "object", properties: { limit: { type: "integer" } } } },
      { name: "whatsapp.list_chats", inputSchema: { type: "object", properties: { limit: { type: "integer" } } } },
      { name: "whatsapp.get_auth_status", inputSchema: { type: "object", properties: {} } },
      { name: "whatsapp.get_messages", inputSchema: { type: "object", properties: { jid: { type: "string" }, limit: { type: "integer" } }, required: ["jid"] } },
      { name: "whatsapp.send_message", inputSchema: { type: "object", properties: { jid: { type: "string" }, text: { type: "string" } }, required: ["jid", "text"] } },
      { name: "whatsapp.mark_read", inputSchema: { type: "object", properties: { jid: { type: "string" }, message_id: { type: "string" } }, required: ["jid"] } },
      { name: "whatsapp.get_updates", inputSchema: { type: "object", properties: { limit: { type: "integer" } } } },
      { name: "imessage.list_chats", inputSchema: { type: "object", properties: { limit: { type: "integer" } } } },
      { name: "imessage.get_auth_status", inputSchema: { type: "object", properties: {} } },
      { name: "imessage.get_messages", inputSchema: { type: "object", properties: { chat_id: { type: "string" }, limit: { type: "integer" } }, required: ["chat_id"] } },
      { name: "imessage.send_message", inputSchema: { type: "object", properties: { chat_id: { type: "string" }, text: { type: "string" } }, required: ["chat_id", "text"] } },
      { name: "imessage.get_updates", inputSchema: { type: "object", properties: { limit: { type: "integer" } } } },
    ];
  }

  async mcpCallTool(params) {
    const name = String(params.name || "");
    const arguments_ = params.arguments && typeof params.arguments === "object" ? params.arguments : {};
    let payload;
    if (name === "telegram.list_chats") payload = { ok: true, chats: await this.telegramBridge.getOverviewChats().then((chats) => chats.slice(0, Number(arguments_.limit || 100))) };
    else if (name === "telegram.get_messages") payload = { ok: true, ...(await this.getTelegramChat(arguments_.peer, Number(arguments_.limit || 50))) };
    else if (name === "telegram.search_messages") payload = await this.#telegramSearchMessages(String(arguments_.query || ""), arguments_.peer, Number(arguments_.limit || 20));
    else if (name === "telegram.send_message") payload = await this.#telegramSendMessage(arguments_.peer, String(arguments_.text || ""), arguments_.reply_to_message_id);
    else if (name === "telegram.delete_messages") payload = await this.#telegramDeleteMessages(arguments_.peer, arguments_.message_ids || []);
    else if (name === "telegram.mark_read") payload = await this.#telegramMarkRead(arguments_.peer, Number(arguments_.max_id || 0));
    else if (name === "telegram.list_members") payload = await this.#telegramListMembers(arguments_.peer, Number(arguments_.limit || 100));
    else if (name === "telegram.get_updates") payload = { ok: true, updates: [] };
    else if (name === "whatsapp.list_chats") payload = { ok: true, chats: (await this.whatsapp.authStatus()).chats.slice(0, Number(arguments_.limit || 100)) };
    else if (name === "whatsapp.get_auth_status") payload = await this.getWhatsAppAuth();
    else if (name === "whatsapp.get_messages") payload = await this.getWhatsAppChat(String(arguments_.jid || ""));
    else if (name === "whatsapp.send_message") payload = await this.whatsapp.sendMessage(String(arguments_.jid || ""), String(arguments_.text || ""));
    else if (name === "whatsapp.mark_read") payload = await this.whatsapp.markRead(String(arguments_.jid || ""), arguments_.message_id ? String(arguments_.message_id) : null);
    else if (name === "whatsapp.get_updates") payload = await this.whatsapp.getUpdates(Number(arguments_.limit || 50));
    else if (name === "imessage.list_chats") payload = { ok: true, chats: (await this.getIMessageAuth()).visible_chats.slice(0, Number(arguments_.limit || 100)) };
    else if (name === "imessage.get_auth_status") payload = await this.getIMessageAuth();
    else if (name === "imessage.get_messages") payload = await this.imessage.getChat(String(arguments_.chat_id || ""), Number(arguments_.limit || 50), true);
    else if (name === "imessage.send_message") payload = await this.imessage.sendMessage(String(arguments_.chat_id || ""), String(arguments_.text || ""));
    else if (name === "imessage.get_updates") payload = await this.imessage.getUpdates(Number(arguments_.limit || 50));
    else throw new Error(`Unknown tool: ${name}`);
    return {
      content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
      structuredContent: payload,
      isError: !Boolean(payload.ok ?? true),
    };
  }

  async #telegramSearchMessages(query, peer, limit) {
    const queryText = String(query || "").trim().toLowerCase();
    if (!queryText) throw new Error("Search query is required");
    const dialogs = peer ? [await this.getTelegramChat(peer, Math.max(limit * 3, 100)).then((result) => result.chat ? { ...result.chat, messages: result.messages } : null)] : await this.telegramBridge._getAllowedDialogs();
    const hits = [];
    if (peer) {
      const chat = dialogs[0];
      for (const message of chat?.messages || []) {
        if (String(message.text || "").toLowerCase().includes(queryText)) {
          hits.push({ peer_id: chat.peer_id, chat_title: chat.title, ...message });
          if (hits.length >= limit) break;
        }
      }
      return { ok: true, messages: hits };
    }
    for (const dialog of dialogs) {
      const chat = await this.telegramBridge.getChat(utils.getPeerId(dialog.entity), Math.max(limit * 3, 50));
      for (const message of chat.messages || []) {
        if (String(message.text || "").toLowerCase().includes(queryText)) {
          hits.push({ peer_id: chat.chat?.peer_id, chat_title: chat.chat?.title, ...message });
          if (hits.length >= limit) return { ok: true, messages: hits };
        }
      }
    }
    return { ok: true, messages: hits };
  }

  async #telegramSendMessage(peer, text, replyToMessageId) {
    await this.telegramBridge._ensureClient();
    const chat = await this.telegramBridge.getChat(peer, 1);
    if (!chat.chat) throw new Error("Unknown Telegram chat");
    const sent = await this.telegramBridge.client.sendMessage(chat.chat.peer_id, {
      message: String(text || ""),
      replyTo: replyToMessageId ? Number(replyToMessageId) : undefined,
    });
    return { ok: true, message: this.telegramBridge._serializeMessage(sent) };
  }

  async #telegramDeleteMessages(peer, messageIds) {
    await this.telegramBridge._ensureClient();
    const chat = await this.telegramBridge.getChat(peer, 1);
    if (!chat.chat) throw new Error("Unknown Telegram chat");
    await this.telegramBridge.client.deleteMessages(chat.chat.peer_id, messageIds.map((value) => Number(value)), { revoke: true });
    return { ok: true, deleted: messageIds.map((value) => Number(value)) };
  }

  async #telegramMarkRead(peer, maxId) {
    await this.telegramBridge._ensureClient();
    const chat = await this.telegramBridge.getChat(peer, 1);
    if (!chat.chat) throw new Error("Unknown Telegram chat");
    await this.telegramBridge.client.markAsRead(chat.chat.peer_id, undefined, { maxId: Number(maxId || 0) || undefined });
    return { ok: true, peer_id: chat.chat.peer_id, max_id: Number(maxId || 0) };
  }

  async #telegramListMembers(peer, limit) {
    await this.telegramBridge._ensureClient();
    const entity = await this.telegramBridge.client.getInputEntity(String(peer));
    const members = [];
    for await (const user of this.telegramBridge.client.iterParticipants(entity, { limit: Number(limit || 100) })) {
      members.push({
        id: Number(user.id || 0) || null,
        username: user.username || null,
        phone: user.phone || null,
        name: [user.firstName, user.lastName].filter(Boolean).join(" ").trim() || user.username || "Telegram user",
        bot: Boolean(user.bot),
      });
    }
    return { ok: true, members };
  }

  async mcpResources() {
    const resources = [
      { uri: "telegram://config", name: "Proxy configuration", mimeType: "application/json", description: "Configuration and identity summary for the Telegram proxy." },
      { uri: "telegram://chats", name: "Accessible chats", mimeType: "application/json", description: "Chats visible through the Cloud folder policy." },
      { uri: "telegram://updates", name: "Recent updates", mimeType: "application/json", description: "Recent Telegram updates across allowed chats." },
      { uri: "whatsapp://config", name: "WhatsApp configuration", mimeType: "application/json", description: "Status and Cloud-label scope for WhatsApp." },
      { uri: "whatsapp://chats", name: "WhatsApp chats", mimeType: "application/json", description: "WhatsApp chats currently carrying the Cloud label." },
      { uri: "whatsapp://updates", name: "WhatsApp updates", mimeType: "application/json", description: "Recent WhatsApp message events across allowed chats." },
    ];
    if (this.config.imessageEnabled) {
      resources.push(
        { uri: "imessage://config", name: "Messages configuration", mimeType: "application/json", description: "Status for the local Messages bridge." },
        { uri: "imessage://chats", name: "Messages chats", mimeType: "application/json", description: "Locally visible Messages chats exposed through MCP." },
        { uri: "imessage://updates", name: "Messages updates", mimeType: "application/json", description: "Recent Messages events across visible chats." },
      );
    }
    for (const chat of await this.telegramBridge.getOverviewChats().catch(() => [])) {
      resources.push({ uri: `telegram://chat/${chat.peer_id}`, name: chat.title, mimeType: "application/json", description: `Recent messages for ${chat.title}.` });
    }
    for (const chat of (await this.getWhatsAppAuth()).chats || []) {
      resources.push({ uri: `whatsapp://chat/${encodeURIComponent(chat.jid)}`, name: chat.title, mimeType: "application/json", description: `Recent WhatsApp messages for ${chat.title}.` });
    }
    if (this.config.imessageEnabled) {
      for (const chat of (await this.getIMessageAuth()).visible_chats || []) {
        resources.push({ uri: `imessage://chat/${encodeURIComponent(chat.chat_id)}`, name: chat.title, mimeType: "application/json", description: `Recent Messages content for ${chat.title}.` });
      }
    }
    return resources;
  }

  async mcpReadResource(params) {
    const uri = String(params.uri || "");
    let payload;
    if (uri === "telegram://config") payload = { ok: true, config: (await this.getOverview()).config, upstream: (await this.getOverview()).upstream };
    else if (uri === "telegram://chats") payload = { ok: true, chats: await this.telegramBridge.getOverviewChats() };
    else if (uri === "telegram://updates") payload = { ok: true, updates: [] };
    else if (uri.startsWith("telegram://chat/")) payload = await this.getTelegramChat(decodeURIComponent(uri.slice("telegram://chat/".length)), 50);
    else if (uri === "whatsapp://config") payload = await this.getWhatsAppAuth();
    else if (uri === "whatsapp://chats") payload = { ok: true, chats: (await this.getWhatsAppAuth()).chats || [] };
    else if (uri === "whatsapp://updates") payload = await this.whatsapp.getUpdates(50);
    else if (uri.startsWith("whatsapp://chat/")) payload = await this.getWhatsAppChat(decodeURIComponent(uri.slice("whatsapp://chat/".length)));
    else if (uri === "imessage://config") payload = await this.getIMessageAuth();
    else if (uri === "imessage://chats") payload = { ok: true, chats: (await this.getIMessageAuth()).visible_chats || [] };
    else if (uri === "imessage://updates") payload = await this.imessage.getUpdates(50);
    else if (uri.startsWith("imessage://chat/")) payload = await this.imessage.getChat(decodeURIComponent(uri.slice("imessage://chat/".length)), 50, true);
    else throw new Error(`Unknown resource: ${uri}`);
    return { contents: [{ uri, mimeType: "application/json", text: JSON.stringify(payload, null, 2) }] };
  }
}
