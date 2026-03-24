import fs from "node:fs";
import { promises as fsPromises } from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const TEXT_MIME_TYPES = new Set([
  "application/json",
  "application/ld+json",
  "application/xml",
  "application/javascript",
  "application/x-javascript",
  "application/typescript",
  "application/x-typescript",
  "application/yaml",
  "application/x-yaml",
  "image/svg+xml",
]);

const WORD_MIME_TYPES = new Set([
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);

const SPREADSHEET_PREVIEW_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]);

const PRESENTATION_PREVIEW_MIME_TYPES = new Set([
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]);

const MIME_TYPES_BY_EXTENSION = new Map([
  [".pdf", "application/pdf"],
  [".doc", "application/msword"],
  [".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  [".xls", "application/vnd.ms-excel"],
  [".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
  [".ppt", "application/vnd.ms-powerpoint"],
  [".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
  [".json", "application/json"],
  [".txt", "text/plain"],
  [".md", "text/markdown"],
  [".markdown", "text/markdown"],
  [".csv", "text/csv"],
  [".tsv", "text/tab-separated-values"],
  [".xml", "application/xml"],
  [".yaml", "application/yaml"],
  [".yml", "application/yaml"],
  [".js", "application/javascript"],
  [".mjs", "application/javascript"],
  [".cjs", "application/javascript"],
  [".ts", "application/typescript"],
  [".tsx", "application/typescript"],
  [".jsx", "text/jsx"],
  [".html", "text/html"],
  [".css", "text/css"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".gif", "image/gif"],
  [".webp", "image/webp"],
  [".mp3", "audio/mpeg"],
  [".wav", "audio/wav"],
  [".mp4", "video/mp4"],
  [".zip", "application/zip"],
]);

function mimeTypeForPath(filePath) {
  return MIME_TYPES_BY_EXTENSION.get(path.extname(String(filePath || "")).toLowerCase()) || "application/octet-stream";
}

function textResourceUriForPath(filePath) {
  return `filesystem://file/${encodeURIComponent(filePath)}`;
}

function binaryResourceUriForPath(filePath) {
  return `filesystem://binary/${encodeURIComponent(filePath)}`;
}

function previewResourceUriForPath(filePath) {
  return `filesystem://preview/${encodeURIComponent(filePath)}`;
}

function isTextMimeType(mimeType) {
  const normalized = String(mimeType || "").toLowerCase();
  return normalized.startsWith("text/") || TEXT_MIME_TYPES.has(normalized);
}

function isWordMimeType(mimeType) {
  return WORD_MIME_TYPES.has(String(mimeType || "").toLowerCase());
}

function isSpreadsheetPreviewMimeType(mimeType) {
  return SPREADSHEET_PREVIEW_MIME_TYPES.has(String(mimeType || "").toLowerCase());
}

function isPresentationPreviewMimeType(mimeType) {
  return PRESENTATION_PREVIEW_MIME_TYPES.has(String(mimeType || "").toLowerCase());
}

function isPreviewableMimeType(mimeType) {
  const normalized = String(mimeType || "").toLowerCase();
  return normalized === "application/pdf"
    || isTextMimeType(normalized)
    || isWordMimeType(normalized)
    || isSpreadsheetPreviewMimeType(normalized)
    || isPresentationPreviewMimeType(normalized);
}

function unsupportedPreviewError(filePath, mimeType) {
  return new Error(
    `Preview is only available for text, PDF, Word (.doc/.docx), Excel (.xlsx), and PowerPoint (.pptx) files. ${filePath} is ${mimeType || "an unsupported file type"}. Use filesystem.read_binary_file or filesystem://binary/<path> instead.`,
  );
}

function normalizePreviewText(text) {
  return String(text || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u2028\u2029]/g, "\n")
    .replace(/\u0000/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function isLikelyLowQualityPdfLine(line) {
  const text = String(line || "").trim();
  if (text.length < 8) {
    return false;
  }
  const glyphs = [...text].filter((char) => !/\s/u.test(char));
  if (!glyphs.length) {
    return false;
  }
  const weirdGlyphs = glyphs.filter((char) => !/[\p{L}\p{N}.,:;()\-+/%<>=_"'!?№&@]/u.test(char)).length;
  return weirdGlyphs / glyphs.length >= 0.18;
}

function cleanPdfPageText(pageText) {
  const lines = normalizePreviewText(pageText).split("\n");
  let removed = 0;
  while (lines.length && removed < 4 && isLikelyLowQualityPdfLine(lines[0])) {
    lines.shift();
    removed += 1;
  }
  return lines.join("\n").trim();
}

const PDF_PAGE_BREAK_SENTINEL = "<<<AARDVARK_PAGE_BREAK>>>";
const SWIFT_PDF_PREVIEW_SCRIPT = `
import Foundation
import PDFKit

let separator = "${PDF_PAGE_BREAK_SENTINEL}"
guard CommandLine.arguments.count > 1 else {
  FileHandle.standardError.write(Data("Missing PDF path.\\n".utf8))
  exit(2)
}

let fileURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let document = PDFDocument(url: fileURL) else {
  FileHandle.standardError.write(Data("Could not open PDF.\\n".utf8))
  exit(1)
}

for pageIndex in 0..<document.pageCount {
  if pageIndex > 0 {
    FileHandle.standardOutput.write(Data((separator + "\\n").utf8))
  }
  let pageText = document.page(at: pageIndex)?.string ?? ""
  FileHandle.standardOutput.write(Data(pageText.utf8))
}
`;

export function convertPdfTextToMarkdown(filePath, extractedText) {
  const pages = String(extractedText || "")
    .split(PDF_PAGE_BREAK_SENTINEL)
    .map((page) => cleanPdfPageText(page))
    .filter(Boolean);
  const title = path.basename(filePath, path.extname(filePath));
  const pageSections = pages.length
    ? pages.map((page, index) => `## Page ${index + 1}\n\n${page}`).join("\n\n")
    : "_No text could be extracted from this PDF._";
  return `# ${title}\n\n${pageSections}`.trim();
}

function defaultPdfPreviewProvider(filePath) {
  const output = execFileSync("/usr/bin/swift", ["-", filePath], {
    input: SWIFT_PDF_PREVIEW_SCRIPT,
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
  return convertPdfTextToMarkdown(filePath, output);
}

function defaultWordPreviewProvider(filePath) {
  return normalizePreviewText(execFileSync("/usr/bin/textutil", ["-convert", "txt", "-stdout", filePath], {
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  }));
}

function decodeXmlEntities(text) {
  return String(text || "").replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos);/g, (_match, entity) => {
    const normalized = String(entity || "").toLowerCase();
    if (normalized === "amp") return "&";
    if (normalized === "lt") return "<";
    if (normalized === "gt") return ">";
    if (normalized === "quot") return "\"";
    if (normalized === "apos") return "'";
    if (normalized.startsWith("#x")) {
      return String.fromCodePoint(parseInt(normalized.slice(2), 16));
    }
    if (normalized.startsWith("#")) {
      return String.fromCodePoint(parseInt(normalized.slice(1), 10));
    }
    return "";
  });
}

function listZipEntries(zipPath) {
  const output = execFileSync("/usr/bin/unzip", ["-Z1", zipPath], {
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
  });
  return output.split(/\r?\n/).filter(Boolean);
}

function readZipEntryText(zipPath, entryPath) {
  try {
    return execFileSync("/usr/bin/unzip", ["-p", zipPath, entryPath], {
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
    });
  } catch {
    return null;
  }
}

function xmlAttribute(attributes, name) {
  const match = String(attributes || "").match(new RegExp(`${name}="([^"]*)"`, "i"));
  return match?.[1] || "";
}

function columnIndexFromCellRef(reference) {
  const letters = String(reference || "").toUpperCase().match(/^[A-Z]+/)?.[0] || "";
  let result = 0;
  for (const letter of letters) {
    result = (result * 26) + (letter.charCodeAt(0) - 64);
  }
  return Math.max(result - 1, 0);
}

function joinTabularRow(values) {
  const cloned = [...values];
  while (cloned.length && !String(cloned[cloned.length - 1] || "").length) {
    cloned.pop();
  }
  return cloned.map((value) => String(value ?? "")).join("\t");
}

function resolveZipTarget(baseDir, target) {
  const normalizedTarget = String(target || "").replace(/^\/+/, "");
  return path.posix.normalize(target.startsWith("/") ? normalizedTarget : path.posix.join(baseDir, normalizedTarget));
}

function parseSharedStrings(xml) {
  return [...String(xml || "").matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/g)].map((match) => decodeXmlEntities(
    [...match[1].matchAll(/<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g)]
      .map((item) => item[1])
      .join(""),
  ));
}

function parseSpreadsheetCellValue(attributes, cellXml, sharedStrings) {
  const cellType = xmlAttribute(attributes, "t");
  if (cellType === "inlineStr") {
    return decodeXmlEntities(
      [...String(cellXml || "").matchAll(/<t(?:\s[^>]*)?>([\s\S]*?)<\/t>/g)]
        .map((item) => item[1])
        .join(""),
    );
  }
  const rawValue = String(cellXml || "").match(/<v(?:\s[^>]*)?>([\s\S]*?)<\/v>/)?.[1] || "";
  if (cellType === "s") {
    return sharedStrings[Number(rawValue)] || "";
  }
  if (cellType === "b") {
    return rawValue === "1" ? "TRUE" : "FALSE";
  }
  return decodeXmlEntities(rawValue);
}

function parseWorkbookSheetEntries(zipPath) {
  const zipEntries = listZipEntries(zipPath);
  const workbookXml = readZipEntryText(zipPath, "xl/workbook.xml");
  const workbookRelsXml = readZipEntryText(zipPath, "xl/_rels/workbook.xml.rels");
  if (workbookXml && workbookRelsXml) {
    const relationships = new Map(
      [...workbookRelsXml.matchAll(/<Relationship\b[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"/g)]
        .map((match) => [match[1], resolveZipTarget("xl", match[2])]),
    );
    const sheets = [...workbookXml.matchAll(/<sheet\b([^>]*)\/?>/g)]
      .map((match) => {
        const name = decodeXmlEntities(xmlAttribute(match[1], "name")) || "Sheet";
        const relationshipId = xmlAttribute(match[1], "r:id");
        const entryPath = relationships.get(relationshipId);
        if (!entryPath) {
          return null;
        }
        return { name, entryPath };
      })
      .filter(Boolean);
    if (sheets.length) {
      return sheets;
    }
  }
  return zipEntries
    .filter((entry) => /^xl\/worksheets\/sheet\d+\.xml$/i.test(entry))
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))
    .map((entry, index) => ({ name: `Sheet ${index + 1}`, entryPath: entry }));
}

function convertSpreadsheetToMarkdown(filePath, sheets) {
  const title = path.basename(filePath, path.extname(filePath));
  const sections = sheets.length
    ? sheets.map((sheet) => {
        const body = sheet.rows.length
          ? ["```tsv", ...sheet.rows, "```"].join("\n")
          : "_No cells with previewable text were found in this sheet._";
        return `## ${sheet.name}\n\n${body}`;
      }).join("\n\n")
    : "_No worksheets could be parsed from this spreadsheet._";
  return `# ${title}\n\n${sections}`.trim();
}

function defaultSpreadsheetPreviewProvider(filePath) {
  const sharedStrings = parseSharedStrings(readZipEntryText(filePath, "xl/sharedStrings.xml"));
  const sheets = parseWorkbookSheetEntries(filePath).map(({ name, entryPath }) => {
    const xml = readZipEntryText(filePath, entryPath) || "";
    const rows = [...xml.matchAll(/<row\b[^>]*>([\s\S]*?)<\/row>/g)].map((rowMatch) => {
      const values = [];
      for (const cellMatch of rowMatch[1].matchAll(/<c\b([^>]*)>([\s\S]*?)<\/c>/g)) {
        const cellRef = xmlAttribute(cellMatch[1], "r");
        const value = parseSpreadsheetCellValue(cellMatch[1], cellMatch[2], sharedStrings);
        values[columnIndexFromCellRef(cellRef)] = normalizePreviewText(value).replace(/\n+/g, " ");
      }
      return joinTabularRow(values);
    }).filter((row) => row.length > 0);
    return { name, rows };
  });
  return convertSpreadsheetToMarkdown(filePath, sheets);
}

function extractSlideText(xml) {
  const paragraphs = [...String(xml || "").matchAll(/<a:p\b[^>]*>([\s\S]*?)<\/a:p>/g)]
    .map((match) => {
      const withBreaks = match[1].replace(/<a:br\s*\/>/g, "\n");
      const text = decodeXmlEntities(
        [...withBreaks.matchAll(/<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g)]
          .map((item) => item[1])
          .join(""),
      );
      return normalizePreviewText(text);
    })
    .filter(Boolean);
  if (paragraphs.length) {
    return paragraphs.join("\n\n");
  }
  return normalizePreviewText(decodeXmlEntities(
    [...String(xml || "").matchAll(/<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g)]
      .map((item) => item[1])
      .join(" "),
  ));
}

function defaultPresentationPreviewProvider(filePath) {
  const slides = listZipEntries(filePath)
    .filter((entry) => /^ppt\/slides\/slide\d+\.xml$/i.test(entry))
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }))
    .map((entry, index) => {
      const text = extractSlideText(readZipEntryText(filePath, entry));
      return `## Slide ${index + 1}\n\n${text || "_No text could be extracted from this slide._"}`;
    });
  const title = path.basename(filePath, path.extname(filePath));
  return `# ${title}\n\n${slides.length ? slides.join("\n\n") : "_No slides could be parsed from this presentation._"}`.trim();
}


function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
  return dirPath;
}

function normalizePathValue(value) {
  return path.resolve(String(value || "").trim());
}

function isWithinRoot(rootPath, targetPath) {
  const relative = path.relative(rootPath, targetPath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function clipLines(text, { head = null, tail = null } = {}) {
  if (!text || (head == null && tail == null)) {
    return text;
  }
  const lines = String(text).split(/\r?\n/);
  if (Number.isFinite(head) && head >= 0) {
    return lines.slice(0, head).join("\n");
  }
  if (Number.isFinite(tail) && tail >= 0) {
    return lines.slice(Math.max(lines.length - tail, 0)).join("\n");
  }
  return text;
}

export class NativeFilesystemBridge {
  constructor({
    settingsPath,
    previewCache = null,
    pdfPreviewProvider = defaultPdfPreviewProvider,
    wordPreviewProvider = defaultWordPreviewProvider,
    spreadsheetPreviewProvider = defaultSpreadsheetPreviewProvider,
    presentationPreviewProvider = defaultPresentationPreviewProvider,
  } = {}) {
    this.settingsPath = path.resolve(String(settingsPath || ""));
    this.previewCache = previewCache;
    this.pdfPreviewProvider = pdfPreviewProvider;
    this.wordPreviewProvider = wordPreviewProvider;
    this.spreadsheetPreviewProvider = spreadsheetPreviewProvider;
    this.presentationPreviewProvider = presentationPreviewProvider;
    ensureDir(path.dirname(this.settingsPath));
    this.directoryEntries = this.#loadDirectoryEntries();
  }

  #loadDirectoryEntries() {
    try {
      const payload = JSON.parse(fs.readFileSync(this.settingsPath, "utf8"));
      if (!Array.isArray(payload?.directories)) {
        return [];
      }
      const unique = new Map();
      for (const entry of payload.directories) {
        const normalizedPath = normalizePathValue(entry?.path);
        if (!normalizedPath || unique.has(normalizedPath)) {
          continue;
        }
        unique.set(normalizedPath, {
          path: normalizedPath,
          enabled: entry?.enabled !== false,
        });
      }
      return [...unique.values()];
    } catch {
      return [];
    }
  }

  #saveDirectoryEntries() {
    ensureDir(path.dirname(this.settingsPath));
    fs.writeFileSync(this.settingsPath, `${JSON.stringify({
      directories: this.directoryEntries.map((entry) => ({
        path: entry.path,
        enabled: entry.enabled !== false,
      })),
    }, null, 2)}\n`, "utf8");
  }

  async #canonicalizeDirectory(dirPath) {
    const normalizedPath = normalizePathValue(dirPath);
    if (!normalizedPath) {
      throw new Error("Directory path is required");
    }
    const realPath = await fsPromises.realpath(normalizedPath);
    const stats = await fsPromises.stat(realPath);
    if (!stats.isDirectory()) {
      throw new Error("Selected path is not a directory");
    }
    await fsPromises.access(realPath, fs.constants.R_OK);
    return realPath;
  }

  async #directoryStatus(entry) {
    const normalizedPath = normalizePathValue(entry?.path);
    let resolvedPath = normalizedPath;
    let exists = false;
    let readable = false;
    let error = null;
    try {
      resolvedPath = await fsPromises.realpath(normalizedPath);
      const stats = await fsPromises.stat(resolvedPath);
      exists = stats.isDirectory();
      if (!exists) {
        error = "Directory no longer exists";
      } else {
        await fsPromises.access(resolvedPath, fs.constants.R_OK);
        readable = true;
      }
    } catch (cause) {
      error = cause?.message || String(cause);
    }
    return {
      path: normalizedPath,
      resolved_path: resolvedPath,
      name: path.basename(resolvedPath || normalizedPath) || normalizedPath,
      enabled: entry?.enabled !== false,
      exists,
      readable,
      shared: entry?.enabled !== false && exists && readable,
      error,
    };
  }

  async getStatus() {
    const directories = await Promise.all(this.directoryEntries.map((entry) => this.#directoryStatus(entry)));
    return {
      ok: true,
      available: true,
      read_only: true,
      directories,
      accessible_directories: directories.filter((entry) => entry.shared),
    };
  }

  async hasAccessibleDirectories() {
    return (await this.#accessibleRoots()).length > 0;
  }

  async assertMcpEnabled() {
    if (await this.hasAccessibleDirectories()) {
      return;
    }
    const error = new Error("Filesystem MCP access is disabled until at least one shared directory is enabled");
    error.status = 403;
    throw error;
  }

  async addDirectory(dirPath) {
    const canonicalPath = await this.#canonicalizeDirectory(dirPath);
    const existing = this.directoryEntries.find((entry) => entry.path === canonicalPath);
    if (existing) {
      existing.enabled = true;
    } else {
      this.directoryEntries.push({ path: canonicalPath, enabled: true });
      this.directoryEntries.sort((left, right) => left.path.localeCompare(right.path));
    }
    this.#saveDirectoryEntries();
    return this.getStatus();
  }

  async setDirectoryEnabled(dirPath, enabled) {
    const normalizedPath = normalizePathValue(dirPath);
    const entry = this.directoryEntries.find((item) => item.path === normalizedPath);
    if (!entry) {
      throw new Error(`Unknown shared directory: ${normalizedPath}`);
    }
    entry.enabled = Boolean(enabled);
    this.#saveDirectoryEntries();
    return this.getStatus();
  }

  async removeDirectory(dirPath) {
    const normalizedPath = normalizePathValue(dirPath);
    const before = this.directoryEntries.length;
    this.directoryEntries = this.directoryEntries.filter((entry) => entry.path !== normalizedPath);
    if (this.directoryEntries.length === before) {
      throw new Error(`Unknown shared directory: ${normalizedPath}`);
    }
    this.#saveDirectoryEntries();
    return this.getStatus();
  }

  async #accessibleRoots() {
    return (await this.getStatus()).accessible_directories
      .map((entry) => entry.resolved_path)
      .filter(Boolean)
      .sort((left, right) => right.length - left.length);
  }

  async #resolveAllowedPath(targetPath, { kind = "any" } = {}) {
    const normalizedPath = normalizePathValue(targetPath);
    if (!normalizedPath) {
      throw new Error("Path is required");
    }
    let resolvedPath;
    try {
      resolvedPath = await fsPromises.realpath(normalizedPath);
    } catch {
      throw new Error("Path does not exist");
    }
    const roots = await this.#accessibleRoots();
    const rootPath = roots.find((candidate) => isWithinRoot(candidate, resolvedPath));
    if (!rootPath) {
      throw new Error("Path is outside the shared directories");
    }
    const stats = await fsPromises.stat(resolvedPath);
    if (kind === "directory" && !stats.isDirectory()) {
      throw new Error("Path is not a directory");
    }
    if (kind === "file" && !stats.isFile()) {
      throw new Error("Path is not a file");
    }
    return {
      path: resolvedPath,
      root_path: rootPath,
      relative_path: path.relative(rootPath, resolvedPath) || ".",
      stats,
    };
  }

  async listDirectory(dirPath) {
    const directory = await this.#resolveAllowedPath(dirPath, { kind: "directory" });
    const entries = [];
    for (const dirent of await fsPromises.readdir(directory.path, { withFileTypes: true })) {
      if (dirent.name === ".DS_Store") {
        continue;
      }
      const candidatePath = path.join(directory.path, dirent.name);
      let resolvedPath = null;
      try {
        resolvedPath = await fsPromises.realpath(candidatePath);
      } catch {
        continue;
      }
      if (!isWithinRoot(directory.root_path, resolvedPath)) {
        continue;
      }
      const stats = await fsPromises.stat(resolvedPath);
      const mimeType = stats.isDirectory() ? null : mimeTypeForPath(resolvedPath);
      entries.push({
        name: dirent.name,
        path: resolvedPath,
        kind: stats.isDirectory() ? "directory" : "file",
        size: stats.isDirectory() ? null : stats.size,
        modified_at: stats.mtime.toISOString(),
        mime_type: mimeType,
        resource_uri: stats.isDirectory()
          ? `filesystem://directory/${encodeURIComponent(resolvedPath)}`
          : (isTextMimeType(mimeType) ? textResourceUriForPath(resolvedPath) : null),
        binary_resource_uri: stats.isDirectory() ? null : binaryResourceUriForPath(resolvedPath),
        preview_resource_uri: stats.isDirectory() || !isPreviewableMimeType(mimeType) ? null : previewResourceUriForPath(resolvedPath),
      });
    }
    entries.sort((left, right) => {
      if (left.kind !== right.kind) {
        return left.kind === "directory" ? -1 : 1;
      }
      return left.name.localeCompare(right.name);
    });
    return {
      ok: true,
      directory: {
        path: directory.path,
        root_path: directory.root_path,
        relative_path: directory.relative_path,
      },
      entries,
    };
  }

  async getFileInfo(filePath) {
    const file = await this.#resolveAllowedPath(filePath);
    const mimeType = file.stats.isFile() ? mimeTypeForPath(file.path) : null;
    return {
      ok: true,
      path: file.path,
      root_path: file.root_path,
      relative_path: file.relative_path,
      kind: file.stats.isDirectory() ? "directory" : (file.stats.isFile() ? "file" : "other"),
      size: file.stats.isFile() ? file.stats.size : null,
      modified_at: file.stats.mtime.toISOString(),
      created_at: file.stats.birthtime?.toISOString?.() || null,
      mime_type: mimeType,
      can_read_text: file.stats.isFile() && isTextMimeType(mimeType),
      can_read_binary: file.stats.isFile(),
      can_preview: file.stats.isFile() && isPreviewableMimeType(mimeType),
      text_resource_uri: file.stats.isFile() && isTextMimeType(mimeType) ? textResourceUriForPath(file.path) : null,
      binary_resource_uri: file.stats.isFile() ? binaryResourceUriForPath(file.path) : null,
      preview_resource_uri: file.stats.isFile() && isPreviewableMimeType(mimeType) ? previewResourceUriForPath(file.path) : null,
      read_only: true,
    };
  }

  async readTextFile(filePath, { head = null, tail = null } = {}) {
    const file = await this.#resolveAllowedPath(filePath, { kind: "file" });
    const mimeType = mimeTypeForPath(file.path);
    if (!isTextMimeType(mimeType)) {
      throw new Error(
        `Path is not a text file. ${file.path} is ${mimeType}. Use filesystem.preview_file or filesystem.read_binary_file instead.`,
      );
    }
    const text = await fsPromises.readFile(file.path, "utf8");
    return {
      ok: true,
      path: file.path,
      root_path: file.root_path,
      relative_path: file.relative_path,
      text: clipLines(text, {
        head: Number.isFinite(head) ? head : null,
        tail: Number.isFinite(tail) ? tail : null,
      }),
      read_only: true,
    };
  }

  async readBinaryFile(filePath) {
    const file = await this.#resolveAllowedPath(filePath, { kind: "file" });
    const bytes = await fsPromises.readFile(file.path);
    return {
      ok: true,
      path: file.path,
      root_path: file.root_path,
      relative_path: file.relative_path,
      size: file.stats.size,
      mime_type: mimeTypeForPath(file.path),
      blob: bytes.toString("base64"),
      encoding: "base64",
      read_only: true,
    };
  }

  async readBinaryResource(filePath) {
    const file = await this.readBinaryFile(filePath);
    return {
      contents: [{
        uri: binaryResourceUriForPath(file.path),
        mimeType: file.mime_type,
        blob: file.blob,
      }],
    };
  }

  #clipPreview(preview, { head = null, tail = null } = {}) {
    if (!preview || typeof preview !== "object") {
      return preview;
    }
    return {
      ...preview,
      text: clipLines(preview.text, {
        head: Number.isFinite(head) ? head : null,
        tail: Number.isFinite(tail) ? tail : null,
      }),
    };
  }

  async previewFile(filePath, { head = null, tail = null } = {}) {
    const file = await this.#resolveAllowedPath(filePath, { kind: "file" });
    const mimeType = mimeTypeForPath(file.path);
    const cachedPreview = this.previewCache?.get(file.path, { mtimeMs: file.stats.mtimeMs }) || null;
    if (cachedPreview) {
      return this.#clipPreview(cachedPreview, { head, tail });
    }
    let preview;
    if (mimeType === "application/pdf") {
      const markdown = normalizePreviewText(await this.pdfPreviewProvider(file.path));
      preview = {
        ok: true,
        path: file.path,
        root_path: file.root_path,
        relative_path: file.relative_path,
        source_mime_type: mimeType,
        preview_mime_type: "text/markdown",
        text: markdown,
        converted_locally: true,
        read_only: true,
      };
    } else if (isWordMimeType(mimeType)) {
      const text = normalizePreviewText(await this.wordPreviewProvider(file.path));
      preview = {
        ok: true,
        path: file.path,
        root_path: file.root_path,
        relative_path: file.relative_path,
        source_mime_type: mimeType,
        preview_mime_type: "text/plain",
        text,
        converted_locally: true,
        read_only: true,
      };
    } else if (isSpreadsheetPreviewMimeType(mimeType)) {
      const markdown = normalizePreviewText(await this.spreadsheetPreviewProvider(file.path));
      preview = {
        ok: true,
        path: file.path,
        root_path: file.root_path,
        relative_path: file.relative_path,
        source_mime_type: mimeType,
        preview_mime_type: "text/markdown",
        text: markdown,
        converted_locally: true,
        read_only: true,
      };
    } else if (isPresentationPreviewMimeType(mimeType)) {
      const markdown = normalizePreviewText(await this.presentationPreviewProvider(file.path));
      preview = {
        ok: true,
        path: file.path,
        root_path: file.root_path,
        relative_path: file.relative_path,
        source_mime_type: mimeType,
        preview_mime_type: "text/markdown",
        text: markdown,
        converted_locally: true,
        read_only: true,
      };
    } else if (isTextMimeType(mimeType)) {
      const textPreview = await this.readTextFile(file.path);
      preview = {
        ...textPreview,
        source_mime_type: mimeType,
        preview_mime_type: mimeType,
        converted_locally: false,
      };
    } else {
      throw unsupportedPreviewError(file.path, mimeType);
    }
    this.previewCache?.set(file.path, {
      mtimeMs: file.stats.mtimeMs,
      preview,
    });
    return this.#clipPreview(preview, { head, tail });
  }

  async previewResource(filePath) {
    const preview = await this.previewFile(filePath);
    return {
      contents: [{
        uri: previewResourceUriForPath(preview.path),
        mimeType: preview.preview_mime_type,
        text: preview.text,
      }],
    };
  }

  async mcpTools() {
    if (!(await this.hasAccessibleDirectories())) {
      return [];
    }
    return [
      { name: "filesystem.list_directories", description: "List the local directories currently shared read-only through MCP.", inputSchema: { type: "object", properties: {} } },
      { name: "filesystem.list_directory", description: "List files and subdirectories within a shared directory.", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
      { name: "filesystem.read_text_file", description: "Read a shared text file as UTF-8 text. Rejects binary files; use preview or binary APIs for non-text content.", inputSchema: { type: "object", properties: { path: { type: "string" }, head: { type: "integer" }, tail: { type: "integer" } }, required: ["path"] } },
      { name: "filesystem.read_binary_file", description: "Download any shared file as base64 binary. Prefer this when you need the complete original file.", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
      { name: "filesystem.preview_file", description: "Return a text preview only for text files, PDF, Word (.doc/.docx), Excel (.xlsx), and PowerPoint (.pptx). Previews may be incomplete, so clients should download the binary file for full fidelity.", inputSchema: { type: "object", properties: { path: { type: "string" }, head: { type: "integer" }, tail: { type: "integer" } }, required: ["path"] } },
      { name: "filesystem.get_file_info", description: "Get metadata and available preview/binary resource URIs for a shared file.", inputSchema: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } },
    ];
  }

  async mcpCallTool(name, arguments_ = {}) {
    await this.assertMcpEnabled();
    if (name === "filesystem.list_directories") {
      return { ok: true, ...(await this.getStatus()) };
    }
    if (name === "filesystem.list_directory") {
      return this.listDirectory(String(arguments_.path || ""));
    }
    if (name === "filesystem.read_text_file") {
      return this.readTextFile(String(arguments_.path || ""), {
        head: Number.isFinite(arguments_.head) ? Number(arguments_.head) : null,
        tail: Number.isFinite(arguments_.tail) ? Number(arguments_.tail) : null,
      });
    }
    if (name === "filesystem.read_binary_file") {
      return this.readBinaryFile(String(arguments_.path || ""));
    }
    if (name === "filesystem.preview_file") {
      return this.previewFile(String(arguments_.path || ""), {
        head: Number.isFinite(arguments_.head) ? Number(arguments_.head) : null,
        tail: Number.isFinite(arguments_.tail) ? Number(arguments_.tail) : null,
      });
    }
    if (name === "filesystem.get_file_info") {
      return this.getFileInfo(String(arguments_.path || ""));
    }
    throw new Error(`Unknown filesystem tool: ${name}`);
  }

  async mcpResources() {
    if (!(await this.hasAccessibleDirectories())) {
      return [];
    }
    const status = await this.getStatus();
    return [
      {
        uri: "filesystem://roots",
        name: "Shared directories",
        mimeType: "application/json",
        description: "Directories currently shared read-only through MCP.",
      },
      ...status.accessible_directories.map((entry) => ({
        uri: `filesystem://directory/${encodeURIComponent(entry.path)}`,
        name: entry.name,
        mimeType: "application/json",
        description: `Read-only listing for ${entry.path}.`,
      })),
    ];
  }

  async mcpReadResource(uri) {
    await this.assertMcpEnabled();
    if (uri === "filesystem://roots") {
      return await this.getStatus();
    }
    if (uri.startsWith("filesystem://directory/")) {
      return await this.listDirectory(decodeURIComponent(uri.slice("filesystem://directory/".length)));
    }
    if (uri.startsWith("filesystem://file/")) {
      return await this.readTextFile(decodeURIComponent(uri.slice("filesystem://file/".length)));
    }
    if (uri.startsWith("filesystem://binary/")) {
      return await this.readBinaryResource(decodeURIComponent(uri.slice("filesystem://binary/".length)));
    }
    if (uri.startsWith("filesystem://preview/")) {
      return await this.previewResource(decodeURIComponent(uri.slice("filesystem://preview/".length)));
    }
    throw new Error(`Unknown filesystem resource: ${uri}`);
  }
}
