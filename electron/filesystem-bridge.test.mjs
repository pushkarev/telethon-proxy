import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { promises as fs } from "node:fs";
import { execFileSync } from "node:child_process";

import { NativeFilesystemBridge, convertPdfTextToMarkdown } from "./filesystem-bridge.mjs";


async function makeBridge(options = {}) {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "aardvark-fs-"));
  const settingsPath = path.join(tempRoot, "filesystem-settings.json");
  return {
    tempRoot,
    bridge: new NativeFilesystemBridge({ settingsPath, ...options }),
  };
}

async function zipFixture(rootDir, outputPath) {
  execFileSync("/usr/bin/zip", ["-q", "-r", outputPath, "."], {
    cwd: rootDir,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

test("filesystem bridge tracks selected directories and exposes them read-only", async () => {
  const { tempRoot, bridge } = await makeBridge();
  const sharedDir = path.join(tempRoot, "shared");
  await fs.mkdir(sharedDir);
  await fs.writeFile(path.join(sharedDir, "hello.txt"), "hello\nworld\n", "utf8");
  const canonicalSharedDir = await fs.realpath(sharedDir);

  const statusBefore = await bridge.getStatus();
  assert.deepEqual(statusBefore.accessible_directories, []);

  const statusAfter = await bridge.addDirectory(sharedDir);
  assert.equal(statusAfter.accessible_directories.length, 1);
  assert.equal(statusAfter.accessible_directories[0].path, canonicalSharedDir);

  const listing = await bridge.listDirectory(sharedDir);
  assert.equal(listing.entries.length, 1);
  assert.equal(listing.entries[0].name, "hello.txt");

  const file = await bridge.readTextFile(path.join(sharedDir, "hello.txt"), { head: 1 });
  assert.equal(file.text, "hello");

  const info = await bridge.getFileInfo(path.join(sharedDir, "hello.txt"));
  assert.equal(info.kind, "file");
  assert.equal(info.read_only, true);
  assert.equal(info.can_read_text, true);
  assert.equal(info.can_read_binary, true);
  assert.equal(info.can_preview, true);
});

test("filesystem bridge disables MCP filesystem surface when no directories are shared", async () => {
  const { bridge } = await makeBridge();

  assert.equal(await bridge.hasAccessibleDirectories(), false);
  assert.deepEqual(await bridge.mcpTools(), []);
  assert.deepEqual(await bridge.mcpResources(), []);

  await assert.rejects(
    bridge.mcpCallTool("filesystem.list_directories", {}),
    /Filesystem MCP access is disabled/,
  );
  await assert.rejects(
    bridge.mcpReadResource("filesystem://roots"),
    /Filesystem MCP access is disabled/,
  );
});

test("filesystem bridge blocks traversal outside selected directories", async () => {
  const { tempRoot, bridge } = await makeBridge();
  const sharedDir = path.join(tempRoot, "shared");
  const outsideDir = path.join(tempRoot, "outside");
  await fs.mkdir(sharedDir);
  await fs.mkdir(outsideDir);
  await fs.writeFile(path.join(outsideDir, "secret.txt"), "classified", "utf8");
  await bridge.addDirectory(sharedDir);

  await assert.rejects(
    bridge.readTextFile(path.join(sharedDir, "..", "outside", "secret.txt")),
    /outside the shared directories/,
  );
});

test("filesystem bridge blocks symlink escapes and exposes read-only MCP metadata", async () => {
  const { tempRoot, bridge } = await makeBridge();
  const sharedDir = path.join(tempRoot, "shared");
  const outsideDir = path.join(tempRoot, "outside");
  await fs.mkdir(sharedDir);
  await fs.mkdir(outsideDir);
  await fs.writeFile(path.join(outsideDir, "secret.txt"), "classified", "utf8");
  await fs.symlink(outsideDir, path.join(sharedDir, "escape"));
  await bridge.addDirectory(sharedDir);

  const listing = await bridge.listDirectory(sharedDir);
  assert.equal(listing.entries.length, 0);

  await assert.rejects(
    bridge.readTextFile(path.join(sharedDir, "escape", "secret.txt")),
    /outside the shared directories/,
  );

  const tools = await bridge.mcpTools();
  assert.equal(tools.some((tool) => tool.name === "filesystem.read_text_file"), true);
  assert.equal(tools.some((tool) => tool.name === "filesystem.read_binary_file"), true);

  const resources = await bridge.mcpResources();
  assert.equal(resources.some((resource) => resource.uri === "filesystem://roots"), true);
});

test("filesystem bridge exposes binary files with blob resources for downloads", async () => {
  const { tempRoot, bridge } = await makeBridge();
  const sharedDir = path.join(tempRoot, "shared");
  const pdfPath = path.join(sharedDir, "example.pdf");
  const pdfBytes = Buffer.from("%PDF-1.7\n%\xE2\xE3\xCF\xD3\n1 0 obj\n<<>>\nendobj\n", "binary");
  await fs.mkdir(sharedDir);
  await fs.writeFile(pdfPath, pdfBytes);
  await bridge.addDirectory(sharedDir);

  const info = await bridge.getFileInfo(pdfPath);
  assert.equal(info.mime_type, "application/pdf");
  assert.equal(info.binary_resource_uri, `filesystem://binary/${encodeURIComponent(await fs.realpath(pdfPath))}`);

  const binary = await bridge.readBinaryFile(pdfPath);
  assert.equal(binary.mime_type, "application/pdf");
  assert.equal(binary.encoding, "base64");
  assert.equal(Buffer.from(binary.blob, "base64").equals(pdfBytes), true);

  const resource = await bridge.mcpReadResource(info.binary_resource_uri);
  assert.deepEqual(resource, {
    contents: [{
      uri: info.binary_resource_uri,
      mimeType: "application/pdf",
      blob: pdfBytes.toString("base64"),
    }],
  });

  const listing = await bridge.listDirectory(sharedDir);
  assert.equal(listing.entries[0].binary_resource_uri, info.binary_resource_uri);
});

test("filesystem bridge rejects binary files through read_text_file and hides Finder junk", async () => {
  const { tempRoot, bridge } = await makeBridge();
  const sharedDir = path.join(tempRoot, "shared");
  const pdfPath = path.join(sharedDir, "example.pdf");
  await fs.mkdir(sharedDir);
  await fs.writeFile(path.join(sharedDir, ".DS_Store"), "junk", "utf8");
  await fs.writeFile(pdfPath, Buffer.from("%PDF-1.4\n", "utf8"));
  await bridge.addDirectory(sharedDir);

  const listing = await bridge.listDirectory(sharedDir);
  assert.deepEqual(listing.entries.map((entry) => entry.name), ["example.pdf"]);

  await assert.rejects(
    bridge.readTextFile(pdfPath),
    /Use filesystem\.preview_file or filesystem\.read_binary_file instead\./,
  );
});

test("filesystem bridge previews text and PDF files and suggests binary access for unsupported types", async () => {
  const { tempRoot, bridge } = await makeBridge({
    pdfPreviewProvider: async (filePath) => `# ${path.basename(filePath, ".pdf")}\n\n## Page 1\n\nConverted locally.`,
  });
  const sharedDir = path.join(tempRoot, "shared");
  const textPath = path.join(sharedDir, "notes.txt");
  const pdfPath = path.join(sharedDir, "report.pdf");
  const zipPath = path.join(sharedDir, "archive.zip");
  await fs.mkdir(sharedDir);
  await fs.writeFile(textPath, "hello\nworld\n", "utf8");
  await fs.writeFile(pdfPath, Buffer.from("%PDF-1.4\n", "utf8"));
  await fs.writeFile(zipPath, Buffer.from("PK\x03\x04", "binary"));
  await bridge.addDirectory(sharedDir);

  const textPreview = await bridge.previewFile(textPath, { head: 1 });
  assert.equal(textPreview.preview_mime_type, "text/plain");
  assert.equal(textPreview.text, "hello");
  assert.equal(textPreview.converted_locally, false);

  const pdfPreview = await bridge.previewFile(pdfPath);
  assert.equal(pdfPreview.preview_mime_type, "text/markdown");
  assert.match(pdfPreview.text, /^# report/m);
  assert.equal(pdfPreview.converted_locally, true);

  const textInfo = await bridge.getFileInfo(textPath);
  assert.equal(textInfo.preview_resource_uri, `filesystem://preview/${encodeURIComponent(await fs.realpath(textPath))}`);

  const previewResource = await bridge.mcpReadResource(textInfo.preview_resource_uri);
  assert.deepEqual(previewResource, {
    contents: [{
      uri: textInfo.preview_resource_uri,
      mimeType: "text/plain",
      text: "hello\nworld\n",
    }],
  });

  const tools = await bridge.mcpTools();
  assert.equal(tools.some((tool) => tool.name === "filesystem.preview_file"), true);

  await assert.rejects(
    bridge.previewFile(zipPath),
    /Use filesystem\.read_binary_file or filesystem:\/\/binary\/<path> instead\./,
  );
});

test("filesystem bridge strips noisy garbage lines from the start of PDF previews", async () => {
  const preview = convertPdfTextToMarkdown("/tmp/ocr.pdf", `P*t* l.t g, 1.1 ]"l**1, i!.]*i i{ri},\nXi}ý*ýr:ýit{** trrua{*,, *, {}l. rзр,t\n+;1-ý:]}} ?*ý з} ý1 ll-ý{|t :*0 }#ý-*\nФамилия И.О.: ПУШКАРЕВ Д А\nДата рождения: 10.11.1985`);
  assert.doesNotMatch(preview, /P\*t\*/);
  assert.match(preview, /Фамилия И\.О\.: ПУШКАРЕВ Д А/);
});

test("filesystem bridge previews Word, Excel, and PowerPoint documents", async () => {
  const { tempRoot, bridge } = await makeBridge({
    wordPreviewProvider: async () => "word preview text",
  });
  const sharedDir = path.join(tempRoot, "shared");
  const buildDir = path.join(tempRoot, "build");
  await fs.mkdir(sharedDir);
  await fs.mkdir(buildDir);

  const docxPath = path.join(sharedDir, "memo.docx");
  await fs.writeFile(docxPath, Buffer.from("PK\x03\x04", "binary"));

  const xlsxDir = path.join(buildDir, "xlsx");
  await fs.mkdir(path.join(xlsxDir, "xl", "_rels"), { recursive: true });
  await fs.mkdir(path.join(xlsxDir, "xl", "worksheets"), { recursive: true });
  await fs.writeFile(path.join(xlsxDir, "xl", "workbook.xml"), `<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>
        <sheet name="Summary" sheetId="1" r:id="rId1"/>
      </sheets>
    </workbook>`);
  await fs.writeFile(path.join(xlsxDir, "xl", "_rels", "workbook.xml.rels"), `<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
    </Relationships>`);
  await fs.writeFile(path.join(xlsxDir, "xl", "sharedStrings.xml"), `<?xml version="1.0" encoding="UTF-8"?>
    <sst><si><t>Revenue</t></si><si><t>42</t></si></sst>`);
  await fs.writeFile(path.join(xlsxDir, "xl", "worksheets", "sheet1.xml"), `<?xml version="1.0" encoding="UTF-8"?>
    <worksheet><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row></sheetData></worksheet>`);
  const xlsxPath = path.join(sharedDir, "sheet.xlsx");
  await zipFixture(xlsxDir, xlsxPath);

  const pptxDir = path.join(buildDir, "pptx");
  await fs.mkdir(path.join(pptxDir, "ppt", "slides"), { recursive: true });
  await fs.writeFile(path.join(pptxDir, "ppt", "slides", "slide1.xml"), `<?xml version="1.0" encoding="UTF-8"?>
    <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <a:p><a:r><a:t>Hello deck</a:t></a:r></a:p>
    </p:sld>`);
  const pptxPath = path.join(sharedDir, "slides.pptx");
  await zipFixture(pptxDir, pptxPath);

  await bridge.addDirectory(sharedDir);

  const docPreview = await bridge.previewFile(docxPath);
  assert.equal(docPreview.preview_mime_type, "text/plain");
  assert.equal(docPreview.text, "word preview text");

  const xlsxPreview = await bridge.previewFile(xlsxPath);
  assert.equal(xlsxPreview.preview_mime_type, "text/markdown");
  assert.match(xlsxPreview.text, /## Summary/);
  assert.match(xlsxPreview.text, /Revenue\t42/);

  const pptxPreview = await bridge.previewFile(pptxPath);
  assert.equal(pptxPreview.preview_mime_type, "text/markdown");
  assert.match(pptxPreview.text, /## Slide 1/);
  assert.match(pptxPreview.text, /Hello deck/);

  const xlsxInfo = await bridge.getFileInfo(xlsxPath);
  assert.equal(xlsxInfo.preview_resource_uri, `filesystem://preview/${encodeURIComponent(await fs.realpath(xlsxPath))}`);

  const listing = await bridge.listDirectory(sharedDir);
  const byName = new Map(listing.entries.map((entry) => [entry.name, entry]));
  assert.equal(byName.get("memo.docx")?.preview_resource_uri, `filesystem://preview/${encodeURIComponent(await fs.realpath(docxPath))}`);
  assert.equal(byName.get("sheet.xlsx")?.resource_uri, null);
  assert.equal(byName.get("sheet.xlsx")?.preview_resource_uri, xlsxInfo.preview_resource_uri);
});

test("filesystem bridge caches previews until the file modification time changes", async () => {
  const previewCalls = [];
  const { tempRoot, bridge } = await makeBridge({
    previewCache: {
      entries: new Map(),
      get(filePath, { mtimeMs }) {
        const entry = this.entries.get(filePath);
        return entry && entry.mtimeMs === Math.trunc(mtimeMs) ? entry.preview : null;
      },
      set(filePath, { mtimeMs, preview }) {
        this.entries.set(filePath, { mtimeMs: Math.trunc(mtimeMs), preview });
      },
    },
    pdfPreviewProvider: async (filePath) => {
      previewCalls.push(filePath);
      return "# cached\n\n## Page 1\n\nHello";
    },
  });
  const sharedDir = path.join(tempRoot, "shared");
  const pdfPath = path.join(sharedDir, "cached.pdf");
  await fs.mkdir(sharedDir);
  await fs.writeFile(pdfPath, Buffer.from("%PDF-1.4\n", "utf8"));
  await bridge.addDirectory(sharedDir);

  const first = await bridge.previewFile(pdfPath, { head: 1 });
  const second = await bridge.previewFile(pdfPath, { head: 1 });
  assert.equal(previewCalls.length, 1);
  assert.equal(first.text, "# cached");
  assert.equal(second.text, "# cached");

  await fs.writeFile(pdfPath, Buffer.from("%PDF-1.5\nchanged", "utf8"));
  await bridge.previewFile(pdfPath);
  assert.equal(previewCalls.length, 2);
});
