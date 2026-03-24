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
    assert.equal(reader.labelStateValidated, false);
    assert.equal(reader._cloudLabelRecord(), null);
    assert.equal(reader._isAllowedChat("120363143109861283@g.us"), false);
    reader.labelStateValidated = true;
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
  service.labelStateValidated = true;
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

test("bridge canonicalizes Cloud lid chats to phone-number JIDs after signal mapping refresh", async () => {
  const service = new WhatsAppBridgeService();
  service.labelStateValidated = true;
  service.labels.set("cloud-id", {
    id: "cloud-id",
    name: "Cloud",
    color: 0,
    deleted: false,
    predefinedId: null,
  });
  service.chatLabels.set("139672638480573@lid", new Set(["cloud-id"]));
  service.chats.set("139672638480573@lid", {
    jid: "139672638480573@lid",
    name: "139672638480573",
    unreadCount: 0,
    archived: false,
    lastMessageAt: "2026-03-23T06:53:06.000Z",
    lastMessageText: "Hi",
  });

  await service._refreshLidPnMappings({
    sock: {
      signalRepository: {
        lidMapping: {
          async getPNForLID(lidJid) {
            return lidJid === "139672638480573@lid" ? "6287777274968:0@s.whatsapp.net" : null;
          },
        },
      },
    },
    jids: ["139672638480573@lid"],
  });

  assert.equal(service.lidToPn.get("139672638480573"), "6287777274968");
  assert.equal(service.pnToLid.get("6287777274968"), "139672638480573");
  assert.deepEqual(service._allowedChats(10).map((chat) => chat.jid), ["6287777274968@s.whatsapp.net"]);
  assert.equal(service._allowedChats(10)[0].title, "6287777274968");
  assert.equal(service._allowedChats(10)[0].last_message_text, "Hi");
});

test("bridge returns history for canonical chats when messages were stored under alias JIDs", () => {
  const service = new WhatsAppBridgeService();
  service.labelStateValidated = true;
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
    lastMessageAt: "2026-03-23T15:00:00.000Z",
    lastMessageText: "latest",
  });
  service.messages.set("116063589130297@lid", [
    {
      id: "wamid-1",
      chat_id: "116063589130297@lid",
      from_me: false,
      text: "hello from lid",
      kind: "conversation",
      date: "2026-03-23T14:59:00.000Z",
    },
  ]);

  const messages = service._chatMessages("628213441512@s.whatsapp.net", 10);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, "hello from lid");
});

test("bridge normalizes unsupported WhatsApp messages with stable placeholder text", () => {
  const service = new WhatsAppBridgeService();
  const serialized = service._serializeMessage({
    key: {
      remoteJid: "628213441512@s.whatsapp.net",
      id: "wamid-unsupported",
      fromMe: false,
    },
    message: {
      protocolMessage: { type: 0 },
    },
    messageTimestamp: 1_774_265_200,
  });

  assert.equal(serialized.kind, "protocolMessage");
  assert.equal(serialized.message_type, "protocolMessage");
  assert.equal(serialized.text, "[Unsupported WhatsApp message: protocolMessage]");
});

test("bridge seeds recent history from chat lastMessages payloads", () => {
  const service = new WhatsAppBridgeService();
  service.labelStateValidated = true;
  service.labels.set("cloud-id", {
    id: "cloud-id",
    name: "Cloud",
    color: 0,
    deleted: false,
    predefinedId: null,
  });
  service.chatLabels.set("628213441512@s.whatsapp.net", new Set(["cloud-id"]));

  service._onChats([
    {
      id: "628213441512@s.whatsapp.net",
      name: "Seeded Chat",
      lastMessages: [
        {
          key: {
            remoteJid: "628213441512@s.whatsapp.net",
            id: "wamid-2",
            fromMe: false,
          },
          message: {
            conversation: "seeded from chat snapshot",
          },
          messageTimestamp: 1_774_265_200,
        },
      ],
    },
  ]);

  const [chat] = service._allowedChats(10);
  const messages = service._chatMessages("628213441512@s.whatsapp.net", 10);
  assert.equal(chat.last_message_text, "seeded from chat snapshot");
  assert.equal(chat.last_message_at, "2026-03-23T11:26:40.000Z");
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, "seeded from chat snapshot");
});

test("bridge records history anchors and seeds snapshot message ranges", () => {
  const service = new WhatsAppBridgeService();
  service.labelStateValidated = true;
  service.labels.set("cloud-id", {
    id: "cloud-id",
    name: "Cloud",
    color: 0,
    deleted: false,
    predefinedId: null,
  });
  service.chatLabels.set("139672638480573@lid", new Set(["cloud-id"]));

  service._recordHistoryRange("139672638480573@lid", {
    lastMessageTimestamp: 1_774_248_786,
    messages: [
      {
        key: {
          remoteJid: "139672638480573@lid",
          id: "wamid-anchor",
          fromMe: false,
        },
        message: {
          conversation: "seeded from app-state",
        },
      },
    ],
  });

  const anchor = service._historyAnchorForChat("139672638480573@lid");
  const messages = service._chatMessages("139672638480573@lid", 10);
  assert.equal(anchor?.key?.id, "wamid-anchor");
  assert.equal(anchor?.oldestMsgTimestampMs, 1_774_248_786_000);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, "seeded from app-state");
});

test("bridge persists chats and messages across restarts", async () => {
  const authDir = await fs.mkdtemp(path.join(os.tmpdir(), "wa-bridge-state-"));
  try {
    const writer = new WhatsAppBridgeService({ authDir });
    writer.chats.set("628213441512@s.whatsapp.net", {
      jid: "628213441512@s.whatsapp.net",
      name: "Persisted Chat",
      unreadCount: 0,
      archived: false,
      lastMessageAt: "2026-03-23T11:26:40.000Z",
      lastMessageText: "persisted message",
    });
    writer.messages.set("628213441512@s.whatsapp.net", [
      {
        id: "wamid-3",
        chat_id: "628213441512@s.whatsapp.net",
        from_me: false,
        text: "persisted message",
        kind: "conversation",
        date: "2026-03-23T11:26:40.000Z",
      },
    ]);
    writer.historyAnchors.set("628213441512@s.whatsapp.net", {
      key: {
        remoteJid: "628213441512@s.whatsapp.net",
        id: "wamid-3",
        fromMe: false,
      },
      oldestMsgTimestampMs: 1_774_265_200_000,
    });
    await writer._savePersistedBridgeState();

    const reader = new WhatsAppBridgeService({ authDir });
    await reader._loadPersistedBridgeState();

    assert.equal(reader.chats.get("628213441512@s.whatsapp.net")?.lastMessageText, "persisted message");
    assert.equal(reader.messages.get("628213441512@s.whatsapp.net")?.length, 1);
    assert.equal(reader.messages.get("628213441512@s.whatsapp.net")?.[0]?.text, "persisted message");
    assert.equal(reader.historyAnchors.get("628213441512@s.whatsapp.net")?.key?.id, "wamid-3");
  } finally {
    await fs.rm(authDir, { recursive: true, force: true });
  }
});

test("bridge fetches WhatsApp history on demand when an anchor is available", async () => {
  const service = new WhatsAppBridgeService();
  service.connected = true;
  service.labelStateValidated = true;
  service.labels.set("cloud-id", {
    id: "cloud-id",
    name: "Cloud",
    color: 0,
    deleted: false,
    predefinedId: null,
  });
  service.chatLabels.set("139672638480573@lid", new Set(["cloud-id"]));
  service.historyAnchors.set("139672638480573@lid", {
    key: {
      remoteJid: "139672638480573@lid",
      id: "wamid-anchor",
      fromMe: false,
    },
    oldestMsgTimestampMs: 1_774_248_786_000,
  });
  service.sock = {
    async fetchMessageHistory(limit, key, timestampMs) {
      assert.equal(limit, 50);
      assert.equal(key.id, "wamid-anchor");
      assert.equal(timestampMs, 1_774_248_786_000);
      service._upsertMessage({
        key: {
          remoteJid: "139672638480573@lid",
          id: "wamid-loaded",
          fromMe: false,
        },
        message: {
          conversation: "loaded on click",
        },
        messageTimestamp: 1_774_248_700,
      }, false);
    },
  };

  const messages = await service.ensureChatHistory("139672638480573@lid", 50);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, "loaded on click");
});

test("bridge exposes recent WhatsApp updates with a working limit", async () => {
  const service = new WhatsAppBridgeService();
  service.labelStateValidated = true;
  service.labels.set("cloud-id", {
    id: "cloud-id",
    name: "Cloud",
    color: 0,
    deleted: false,
    predefinedId: null,
  });
  service.chatLabels.set("628213441512@s.whatsapp.net", new Set(["cloud-id"]));
  service.recentUpdates = [
    { chat_id: "628213441512@s.whatsapp.net", message_id: "1" },
    { chat_id: "628213441512@s.whatsapp.net", message_id: "2" },
    { chat_id: "628213441512@s.whatsapp.net", message_id: "3" },
  ];

  const updates = await service.getUpdates(2);
  assert.equal(updates.ok, true);
  assert.deepEqual(updates.updates.map((item) => item.message_id), ["2", "3"]);
});

test("bridge waits briefly for the first WhatsApp QR on a fresh session", async () => {
  const service = new WhatsAppBridgeService();
  service._connect = async () => {
    service.sock = {};
    service.connection = "connecting";
    service.connected = false;
    service.registered = false;
    service.lastError = null;
    service.qrRaw = null;
    service.qrSvg = null;
    setTimeout(() => {
      service.qrRaw = "fresh-qr";
      service.qrSvg = "<svg>fresh-qr</svg>";
      service.qrDataUrl = "data:image/png;base64,fresh";
      service._notifyStatusWaiters();
    }, 10);
  };

  const status = await service.authStatus({ waitForQr: true, timeoutMs: 100 });
  assert.equal(status.qr_available, true);
  assert.equal(status.qr_raw, "fresh-qr");
  assert.equal(status.qr_svg, "<svg>fresh-qr</svg>");
  assert.equal(status.qr_png_data_url, "data:image/png;base64,fresh");
});

test("bridge retries QR generation when the first pairing attempt fails or stays blank", async () => {
  const service = new WhatsAppBridgeService();
  let connectCalls = 0;
  service._connect = async () => {
    connectCalls += 1;
    service.sock = { id: connectCalls };
    service.connection = "connecting";
    service.connected = false;
    service.registered = false;
    service.lastError = connectCalls === 1 ? "Initial registration attempt failed." : null;
    service.qrRaw = null;
    service.qrSvg = null;
    service.qrDataUrl = null;
    if (connectCalls === 2) {
      setTimeout(() => {
        service.qrRaw = "retried-qr";
        service.qrSvg = "<svg>retried-qr</svg>";
        service.qrDataUrl = "data:image/png;base64,retried";
        service._notifyStatusWaiters();
      }, 10);
    }
  };

  const status = await service.requestPairingCode({ timeoutMs: 25 });
  assert.equal(connectCalls, 2);
  assert.equal(status.qr_available, true);
  assert.equal(status.qr_raw, "retried-qr");
  assert.equal(status.qr_png_data_url, "data:image/png;base64,retried");
});

test("bridge reconnect backoff grows and caps", () => {
  const service = new WhatsAppBridgeService();
  assert.equal(service._nextReconnectDelayMs(), 2_000);
  service.reconnectAttempts = 1;
  assert.equal(service._nextReconnectDelayMs(), 4_000);
  service.reconnectAttempts = 2;
  assert.equal(service._nextReconnectDelayMs(), 8_000);
  service.reconnectAttempts = 10;
  assert.equal(service._nextReconnectDelayMs(), 30_000);
});

test("bridge open connection resets reconnect state", async () => {
  const service = new WhatsAppBridgeService();
  const reconnectTimer = setTimeout(() => {}, 60_000);
  const watchdogTimer = setTimeout(() => {}, 60_000);
  const sock = {
    user: { id: "1@s.whatsapp.net", lid: "1@lid", name: "Test User" },
    authState: { creds: { registered: true } },
    async resyncAppState() {},
  };
  service.sock = sock;
  service.reconnectAttempts = 4;
  service.reconnectTimer = reconnectTimer;
  service.connectWatchdogTimer = watchdogTimer;

  await service._onConnectionUpdate(sock, { connection: "open" });

  assert.equal(service.reconnectAttempts, 0);
  assert.equal(service.reconnectTimer, null);
  assert.equal(service.connectWatchdogTimer, null);
});

test("bridge connect watchdog schedules reconnect when connect stalls", () => {
  const service = new WhatsAppBridgeService();
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  const captured = [];
  global.setTimeout = ((fn, delay) => {
    const handle = { fn, delay };
    captured.push(handle);
    return handle;
  });
  global.clearTimeout = (() => {});

  try {
    const sock = {
      ended: false,
      end() {
        this.ended = true;
      },
    };
    service.sock = sock;
    service._armConnectWatchdog(sock);
    assert.equal(captured[0].delay, 25_000);

    captured[0].fn();

    assert.equal(sock.ended, true);
    assert.equal(service.lastError, "WhatsApp connection timed out. Retrying.");
    assert.equal(service.reconnectAttempts, 1);
    assert.equal(captured[1].delay, 2_000);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});
