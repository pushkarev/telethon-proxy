import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { promises as fs } from "node:fs";

import { NativePreviewCache } from "./preview-cache.mjs";


async function makeCache() {
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "aardvark-preview-cache-"));
  return {
    tempRoot,
    dbPath: path.join(tempRoot, "preview-cache.sqlite"),
    cache: new NativePreviewCache({
      dbPath: path.join(tempRoot, "preview-cache.sqlite"),
      encryptionKey: randomBytes(32).toString("base64"),
    }),
  };
}

test("preview cache stores encrypted previews and returns cache hits for matching mtimes", async () => {
  const { dbPath, cache } = await makeCache();
  const filePath = "/tmp/example.pdf";
  const preview = {
    ok: true,
    path: filePath,
    text: "# Example",
    preview_mime_type: "text/markdown",
    source_mime_type: "application/pdf",
    converted_locally: true,
    read_only: true,
  };
  cache.set(filePath, { mtimeMs: 12345, preview });

  assert.deepEqual(cache.get(filePath, { mtimeMs: 12345 }), preview);

  const dbBytes = readFileSync(dbPath);
  assert.equal(dbBytes.includes(Buffer.from("# Example", "utf8")), false);
  assert.equal(dbBytes.includes(Buffer.from(filePath, "utf8")), false);
});

test("preview cache invalidates entries when file modification time changes", async () => {
  const { cache } = await makeCache();
  const filePath = "/tmp/example.txt";
  cache.set(filePath, {
    mtimeMs: 100,
    preview: {
      ok: true,
      path: filePath,
      text: "hello",
      preview_mime_type: "text/plain",
      source_mime_type: "text/plain",
      converted_locally: false,
      read_only: true,
    },
  });

  assert.equal(cache.get(filePath, { mtimeMs: 200 }), null);
  assert.equal(cache.get(filePath, { mtimeMs: 100 }), null);
});
