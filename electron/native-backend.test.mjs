import test from "node:test";
import assert from "node:assert/strict";

import { Api } from "telegram";

import { NativeAppBackend, TelegramAuthManager, telegramErrorRequestsPassword } from "./native-backend.mjs";


function createSecretStore(initial = {}) {
  const state = {
    apiId: "",
    apiHash: "",
    phone: "",
    session: "",
    ...initial,
  };
  return {
    isAvailable: true,
    loadUpstreamSecrets() {
      return { ...state };
    },
    saveUpstreamCredentials({ apiId, apiHash, phone }) {
      state.apiId = String(apiId || "");
      state.apiHash = String(apiHash || "");
      state.phone = String(phone || "");
    },
    saveUpstreamSession(session) {
      state.session = String(session || "");
    },
    clearUpstreamSession() {
      state.session = "";
    },
    clearUpstreamCredentials() {
      state.apiId = "";
      state.apiHash = "";
      state.phone = "";
    },
  };
}

function createConfig() {
  return {
    upstreamApiId: 0,
    upstreamApiHash: "",
    upstreamPhone: "",
    upstreamSessionString: "",
  };
}

function createBridge() {
  return {
    async stop() {},
  };
}

test("telegramErrorRequestsPassword matches multiple password-required error shapes", () => {
  assert.equal(telegramErrorRequestsPassword({ errorMessage: "SESSION_PASSWORD_NEEDED" }), true);
  assert.equal(telegramErrorRequestsPassword({ message: "SessionPasswordNeededError" }), true);
  assert.equal(telegramErrorRequestsPassword({ message: "PHONE_CODE_INVALID" }), false);
});

test("TelegramAuthManager moves to password step when Telegram requires 2FA", async () => {
  const manager = new TelegramAuthManager(createConfig(), createSecretStore(), createBridge());
  manager.pending = {
    apiId: 1,
    apiHash: "hash",
    phone: "+15550000000",
    phoneCodeHash: "code-hash",
    client: {
      async invoke(request) {
        if (request instanceof Api.auth.SignIn) {
          const error = new Error("SESSION_PASSWORD_NEEDED");
          error.errorMessage = "SESSION_PASSWORD_NEEDED";
          throw error;
        }
        if (request instanceof Api.account.GetPassword) {
          return { hint: "pet name" };
        }
        throw new Error(`Unexpected request: ${request?.className || request?.constructor?.name}`);
      },
    },
  };

  const status = await manager.submitCode({ code: "12345" });
  assert.equal(status.next_step, "password");
  assert.equal(status.pending_phone, "+15550000000");
  assert.equal(status.password_hint, "pet name");
  assert.equal(status.last_error, null);
});

test("TelegramAuthManager keeps password step active after a wrong 2FA password", async () => {
  const manager = new TelegramAuthManager(createConfig(), createSecretStore(), createBridge());
  manager.pending = {
    apiId: 1,
    apiHash: "hash",
    phone: "+15550000000",
    phoneCodeHash: "code-hash",
    client: {
      async signInWithPassword(_credentials, handlers) {
        await handlers.onError({ errorMessage: "PASSWORD_HASH_INVALID", message: "PASSWORD_HASH_INVALID" });
        throw new Error("AUTH_USER_CANCEL");
      },
    },
  };
  manager.needsPassword = true;
  manager.passwordHint = "pet name";

  const status = await manager.submitPassword({ password: "wrong-password" });
  assert.equal(status.next_step, "password");
  assert.equal(status.password_hint, "pet name");
  assert.equal(status.last_error, "PASSWORD_HASH_INVALID");
});

test("NativeAppBackend forwards WhatsApp message limits", async () => {
  const backend = Object.create(NativeAppBackend.prototype);
  backend.whatsapp = {
    async ensureChatHistory(jid, limit) {
      assert.equal(jid, "123@s.whatsapp.net");
      assert.equal(limit, 3);
    },
    async authStatus() {
      return {
        chats: [{ jid: "123@s.whatsapp.net", title: "Chat" }],
      };
    },
    _chatMessages(jid, limit) {
      assert.equal(jid, "123@s.whatsapp.net");
      assert.equal(limit, 3);
      return [{ id: "1" }, { id: "2" }, { id: "3" }];
    },
  };

  const result = await backend.getWhatsAppChat("123@s.whatsapp.net", 3);
  assert.equal(result.ok, true);
  assert.equal(result.messages.length, 3);
});

test("NativeAppBackend hides iMessage chat resources when history is unreadable", async () => {
  const backend = Object.create(NativeAppBackend.prototype);
  backend.config = { imessageEnabled: true };
  backend.filesystem = { async mcpResources() { return []; } };
  backend.telegramBridge = { async getOverviewChats() { return []; } };
  backend.getWhatsAppAuth = async () => ({ chats: [] });
  backend.getIMessageAuth = async () => ({
    database_accessible: false,
    visible_chats: [{ chat_id: "chat-1", title: "Visible Chat" }],
  });

  const resources = await backend.mcpResources();
  assert.equal(resources.some((resource) => resource.uri === "imessage://chat/chat-1"), false);
  assert.equal(resources.some((resource) => resource.uri === "imessage://chats"), true);
});
