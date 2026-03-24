import test from "node:test";
import assert from "node:assert/strict";

import { Api } from "telegram";
import { GramJsTelegramBridge, dialogMatchesFilter, serializeDate } from "./gramjs-background.mjs";


function makeDialog({
  isUser = false,
  isGroup = false,
  isChannel = false,
  unreadCount = 0,
  folderId = 0,
  entity = {},
  muteUntil = null,
} = {}) {
  return {
    entity,
    isUser,
    isGroup,
    isChannel,
    unreadCount,
    folderId,
    dialog: {
      folderId,
      notifySettings: { muteUntil },
    },
  };
}

test("dialogMatchesFilter allows explicitly included peers", () => {
  const dialog = makeDialog({
    isUser: true,
    entity: new Api.PeerUser({ userId: 42n }),
  });
  const filter = new Api.DialogFilter({ title: "Cloud" });
  const matched = dialogMatchesFilter(dialog, filter, new Set(["42"]), new Set());
  assert.equal(matched, true);
});

test("dialogMatchesFilter blocks explicitly excluded peers", () => {
  const dialog = makeDialog({
    isUser: true,
    entity: Object.assign(new Api.PeerUser({ userId: 42n }), { contact: true }),
  });
  const filter = new Api.DialogFilter({ title: "Cloud", contacts: true });
  const matched = dialogMatchesFilter(dialog, filter, new Set(["42"]), new Set(["42"]));
  assert.equal(matched, false);
});

test("dialogMatchesFilter respects group filters", () => {
  const dialog = {
    entity: new Api.Channel({ id: 99n, title: "Cloud Group", megagroup: true }),
    isUser: false,
    isGroup: true,
    isChannel: true,
    unreadCount: 3,
    folderId: 0,
    dialog: { folderId: 0, notifySettings: { muteUntil: null } },
  };
  const filter = new Api.DialogFilter({ title: "Cloud", groups: true });
  const matched = dialogMatchesFilter(dialog, filter, new Set(), new Set());
  assert.equal(matched, true);
});

test("serializeDate converts unix seconds from GramJS", () => {
  assert.equal(serializeDate(1774266607), "2026-03-23T11:50:07.000Z");
});

test("telegram bridge cache can be invalidated after writes", () => {
  const bridge = new GramJsTelegramBridge();
  bridge.cachedDialogs = [{ title: "cached" }];
  bridge.cachedAt = Date.now();

  bridge.invalidateCache();

  assert.deepEqual(bridge.cachedDialogs, []);
  assert.equal(bridge.cachedAt, 0);
});
