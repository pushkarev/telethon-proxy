import test from "node:test";
import assert from "node:assert/strict";

import { peerJidsFromLidMappings } from "./service.mjs";

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

