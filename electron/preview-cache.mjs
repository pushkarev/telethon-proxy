import fs from "node:fs";
import path from "node:path";
import { createCipheriv, createDecipheriv, createHmac, randomBytes } from "node:crypto";
import { DatabaseSync } from "node:sqlite";


function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

function normalizeMtimeMs(value) {
  return Math.max(0, Math.trunc(Number(value || 0)));
}

function normalizeLookupPath(filePath) {
  return path.resolve(String(filePath || "").trim());
}

function normalizeKey(encryptionKey) {
  if (!encryptionKey) {
    return null;
  }
  const key = Buffer.isBuffer(encryptionKey)
    ? Buffer.from(encryptionKey)
    : Buffer.from(String(encryptionKey), "base64");
  return key.length === 32 ? key : null;
}

function encodeEncryptedPayload(key, payload) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  const cleartext = Buffer.from(JSON.stringify(payload), "utf8");
  const ciphertext = Buffer.concat([cipher.update(cleartext), cipher.final()]);
  const authTag = cipher.getAuthTag();
  return Buffer.concat([Buffer.from([1]), iv, authTag, ciphertext]);
}

function decodeEncryptedPayload(key, value) {
  const payload = Buffer.isBuffer(value) ? value : Buffer.from(value || []);
  if (payload.length < 1 + 12 + 16) {
    throw new Error("Cached preview payload is malformed");
  }
  const version = payload.readUInt8(0);
  if (version !== 1) {
    throw new Error(`Unsupported cached preview payload version: ${version}`);
  }
  const iv = payload.subarray(1, 13);
  const authTag = payload.subarray(13, 29);
  const ciphertext = payload.subarray(29);
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAuthTag(authTag);
  const cleartext = Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf8");
  return JSON.parse(cleartext);
}

export class NativePreviewCache {
  constructor({ dbPath, encryptionKey } = {}) {
    const cleanDbPath = String(dbPath || "").trim();
    this.dbPath = cleanDbPath ? path.resolve(cleanDbPath) : "";
    this.encryptionKey = normalizeKey(encryptionKey);
    this.enabled = Boolean(this.dbPath && this.encryptionKey);
    this.database = null;
    if (!this.enabled) {
      return;
    }
    ensureDir(path.dirname(this.dbPath));
    this.database = new DatabaseSync(this.dbPath);
    this.database.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = NORMAL;
      CREATE TABLE IF NOT EXISTS preview_cache (
        path_digest TEXT PRIMARY KEY,
        file_mtime_ms INTEGER NOT NULL,
        payload BLOB NOT NULL,
        updated_at INTEGER NOT NULL
      );
    `);
    this.selectStatement = this.database.prepare(`
      SELECT file_mtime_ms, payload
      FROM preview_cache
      WHERE path_digest = ?
    `);
    this.upsertStatement = this.database.prepare(`
      INSERT INTO preview_cache (path_digest, file_mtime_ms, payload, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(path_digest) DO UPDATE SET
        file_mtime_ms = excluded.file_mtime_ms,
        payload = excluded.payload,
        updated_at = excluded.updated_at
    `);
    this.deleteStatement = this.database.prepare(`
      DELETE FROM preview_cache
      WHERE path_digest = ?
    `);
  }

  #pathDigest(filePath) {
    return createHmac("sha256", this.encryptionKey)
      .update(normalizeLookupPath(filePath))
      .digest("hex");
  }

  get(filePath, { mtimeMs } = {}) {
    if (!this.enabled) {
      return null;
    }
    const normalizedPath = normalizeLookupPath(filePath);
    const pathDigest = this.#pathDigest(normalizedPath);
    const row = this.selectStatement.get(pathDigest);
    if (!row) {
      return null;
    }
    if (normalizeMtimeMs(row.file_mtime_ms) !== normalizeMtimeMs(mtimeMs)) {
      this.deleteStatement.run(pathDigest);
      return null;
    }
    try {
      const payload = decodeEncryptedPayload(this.encryptionKey, row.payload);
      if (normalizeLookupPath(payload.path) !== normalizedPath) {
        this.deleteStatement.run(pathDigest);
        return null;
      }
      return payload.preview || null;
    } catch {
      this.deleteStatement.run(pathDigest);
      return null;
    }
  }

  set(filePath, { mtimeMs, preview } = {}) {
    if (!this.enabled || !preview) {
      return;
    }
    const normalizedPath = normalizeLookupPath(filePath);
    const pathDigest = this.#pathDigest(normalizedPath);
    const encryptedPayload = encodeEncryptedPayload(this.encryptionKey, {
      path: normalizedPath,
      preview,
    });
    this.upsertStatement.run(
      pathDigest,
      normalizeMtimeMs(mtimeMs),
      encryptedPayload,
      Date.now(),
    );
  }

  close() {
    this.database?.close();
    this.database = null;
  }
}
