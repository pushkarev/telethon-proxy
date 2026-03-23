import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { promises as fs } from "node:fs";

import { WhatsAppBridgeService, peerJidsFromLidMappings } from "./service.mjs";

test("peerJidsFromLidMappings excludes self and reverse records", () => {
  const actual = peerJidsFromLidMappings(
    [
      ["16506448988", "24176370913413"],
      ["628213441512", "116063589130297"],
      ["905365825678", "23854332272646"],
      ["24176370913413_reverse", "16506448988"],
    ],
    {
      meId: "16506448988:32@s.whatsapp.net",
      meLid: "24176370913413:32@lid",
    },
  );

  assert.deepEqual(actual, ["628213441512@s.whatsapp.net", "905365825678@s.whatsapp.net"]);
});

test("bridge exposes no chats when Cloud label is missing", () => {
  const service = new WhatsAppBridgeService();
  service.chats.set("120363143109861283@g.us", {
    jid: "120363143109861283@g.us",
    name: "Fallback Group",
    unreadCount: 0,
    archived: false,
    lastMessageAt: null,
    lastMessageText: null,
  });
  service.messages.set("120363143109861283@g.us", [{ id: "wamid-1", text: "hello" }]);

  const status = service._status(50);
  assert.equal(status.cloud_label_found, false);
  assert.equal(status.cloud_filter_mode, "label-required");
  assert.deepEqual(status.chats, []);
  assert.throws(() => service._chatMessages("120363143109861283@g.us", 10), /outside Cloud label/);
});

test("bridge persists and reloads label state across restarts", async () => {
  const authDir = await fs.mkdtemp(path.join(os.tmpdir(), "wa-label-state-"));
  try {
    const writer = new WhatsAppBridgeService({ authDir });
    writer.labels.set("cloud-id", {
      id: "cloud-id",
      name: "Cloud",
      color: 123,
      deleted: false,
      predefinedId: null,
    });
    writer.chatLabels.set("120363143109861283@g.us", new Set(["cloud-id"]));
    await writer._savePersistedLabelState();

    const reader = new WhatsAppBridgeService({ authDir });
    await reader._loadPersistedLabelState();

    assert.equal(reader.loadedPersistedLabelState, true);
    assert.equal(reader._cloudLabelRecord()?.id, "cloud-id");
    assert.equal(reader._isAllowedChat("120363143109861283@g.us"), true);
  } finally {
    await fs.rm(authDir, { recursive: true, force: true });
  }
});

test("bridge forces a full app-state rebuild when no persisted labels exist", async () => {
  const service = new WhatsAppBridgeService();
  const calls = [];
  const sock = {
    authState: {
      keys: {
        async set(payload) {
          calls.push({ kind: "set", payload });
        },
      },
    },
    async resyncAppState(collections, isInitialSync) {
      calls.push({ kind: "resync", collections, isInitialSync });
    },
  };

  await service._rebuildLabelStateFromScratch(sock);

  assert.equal(calls[0].kind, "set");
  assert.deepEqual(Object.keys(calls[0].payload["app-state-sync-version"]).sort(), [
    "critical_block",
    "critical_unblock_low",
    "regular",
    "regular_high",
    "regular_low",
  ]);
  assert.equal(calls[1].kind, "resync");
  assert.equal(calls[1].isInitialSync, true);
});

test("bridge matches Cloud chat labels across lid and phone-number JIDs", () => {
  const service = new WhatsAppBridgeService();
  service.labels.set("cloud-id", {
    id: "cloud-id",
    name: "Cloud",
    color: 0,
    deleted: false,
    predefinedId: null,
  });
  service.pnToLid.set("628213441512", "116063589130297");
  service.lidToPn.set("116063589130297", "628213441512");
  service.chatLabels.set("116063589130297@lid", new Set(["cloud-id"]));
  service.chats.set("628213441512@s.whatsapp.net", {
    jid: "628213441512@s.whatsapp.net",
    name: "Mapped Chat",
    unreadCount: 0,
    archived: false,
    lastMessageAt: null,
    lastMessageText: null,
  });

  assert.equal(service._isAllowedChat("628213441512@s.whatsapp.net"), true);
  assert.deepEqual(service._allowedChats(10).map((chat) => chat.jid), ["628213441512@s.whatsapp.net"]);
  assert.deepEqual(service._allowedChats(10)[0].labels, ["Cloud"]);
});
