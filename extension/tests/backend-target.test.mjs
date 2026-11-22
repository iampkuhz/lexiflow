import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { DEFAULT_ORIGIN, parseConnection } from "../build/test-modules/backend-target.js";
import { channelManifest, localApiPort, matchesChannelManifest } from "../scripts/build/extension-build-settings.mjs";

test("connection selection only accepts canonical loopback HTTP with a valid port", () => {
  assert.equal(DEFAULT_ORIGIN, "http://127.0.0.1:18080");
  for (const mode of ["auto", "development", "podman", "default"]) assert.equal(parseConnection({ mode }), undefined);
  assert.deepEqual(parseConnection({ mode: "custom", origin: " http://localhost:65535/ " }), { mode: "custom", origin: "http://localhost:65535" });
  for (const origin of ["http://192.168.1.2:19081", "http://example.com:80", "https://localhost:80", "http://127.1:80",
    "http://2130706433:80", "http://localhost.:80", "http://127.0.0.1:65536", "http://localhost:0", "http://localhost:080",
    "http://user@localhost:90", "http://localhost:90@attacker.test", "http://localhost:90/path", "http://localhost:90/?", "http://localhost:90/#", "http://localhost:90\\", "http://localhost"]) {
    assert.equal(parseConnection({ mode: "custom", origin }), undefined, origin);
  }
  assert.equal(parseConnection({ mode: "auto", origin: "http://localhost:90" }), undefined);
});

test("two stable Chrome slots; neither permissions nor the other slot's identity can be substituted", () => {
  const chromeId = ({ key }) => [...createHash("sha256").update(Buffer.from(key, "base64")).digest("hex").slice(0, 32)]
    .map(hex => String.fromCharCode(97 + parseInt(hex, 16))).join("");
  const formal = channelManifest(18080), preview = channelManifest(18081);
  assert.equal(chromeId(formal), "cgbdoaoalppcklmcbmkdogoagppcimkk");
  assert.equal(localApiPort("19081"), 19081);
  assert.throws(() => localApiPort("19083"));
  assert.deepEqual(channelManifest(19081), preview);
  assert.equal(chromeId(preview), "mnngbjikfoafikclcilgkhhkmcnmlinm");
  assert.equal(formal.name, "LexiFlow");
  assert.equal(preview.name, "LexiFlow 测试版");
  assert.equal(matchesChannelManifest(preview, 18080), false);
  assert.equal(matchesChannelManifest({ ...formal, optional_host_permissions: ["<all_urls>"] }, 18080), false);
  assert.equal(matchesChannelManifest({ ...preview, host_permissions: ["http://192.168.1.2/*"] }, 18081), false);
});
