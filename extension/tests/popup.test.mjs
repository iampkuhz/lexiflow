import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const DIST_POPUP = resolve(import.meta.dirname, "..", "dist", "popup.js");

// ---- Minimal DOM mock ----

function createDocument() {
  const listeners = new Map();
  const elements = new Map();
  return {
    _listeners: listeners,
    _elements: elements,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = listeners.get(type);
      if (arr) {
        const idx = arr.indexOf(fn);
        if (idx >= 0) arr.splice(idx, 1);
      }
    },
    dispatchEvent(event) {
      const arr = listeners.get(event.type) || [];
      for (const fn of arr) fn(event);
      return true;
    },
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, {
          id,
          disabled: id === "enhance-toggle",
          checked: false,
          hidden: id === "status",
          textContent: "",
          _listeners: new Map(),
          addEventListener(type, fn) {
            if (!this._listeners.has(type)) this._listeners.set(type, []);
            this._listeners.get(type).push(fn);
          },
          removeEventListener(type, fn) {
            const arr = this._listeners.get(type);
            if (arr) {
              const idx = arr.indexOf(fn);
              if (idx >= 0) arr.splice(idx, 1);
            }
          },
          dispatchEvent(event) {
            const arr = this._listeners.get(event.type) || [];
            for (const fn of arr) fn(event);
            return true;
          },
        });
      }
      return elements.get(id);
    },
  };
}

// ---- Chrome API mock (Promise-based, matching MV3 no-callback overloads) ----

function createChrome(behaviour = {}) {
  const chromeObj = {
    _calls: [],
    _nextResponse: behaviour.readResponse ?? null,
    _reject: !!behaviour.reject,
    _activeTab: Object.hasOwn(behaviour, "activeTab") ? behaviour.activeTab : { id: 42 },
    tabs: {
      query(q) {
        chromeObj._calls.push({ method: "query", args: { ...q } });
        return Promise.resolve(chromeObj._activeTab ? [chromeObj._activeTab] : []);
      },
      sendMessage(tabId, msg) {
        chromeObj._calls.push({ method: "sendMessage", tabId, message: { ...msg } });
        if (chromeObj._reject) {
          return Promise.reject(new Error("mock send failure"));
        }
        const resp = chromeObj._nextResponse;
        return Promise.resolve(resp);
      },
    },
    runtime: { lastError: null },
  };
  return chromeObj;
}

// ---- helpers ----

function makeContext(doc, chrome) {
  const ctx = {
    document: doc,
    chrome,
    console: { log() {}, error() {}, warn() {} },
    Promise,
    Event,
    setTimeout,
    clearTimeout,
    queueMicrotask,
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  return vm.createContext(ctx);
}

function loadPopup(context) {
  const code = readFileSync(DIST_POPUP, "utf8");
  vm.runInContext(code, context);
}

function fireReady(doc) {
  doc.dispatchEvent(new Event("DOMContentLoaded"));
}

async function flush() {
  for (let i = 0; i < 30; i++) await Promise.resolve();
}

function freshSetup(behaviour = {}) {
  const doc = createDocument();
  const chrome = createChrome(behaviour);
  const ctx = makeContext(doc, chrome);
  loadPopup(ctx);
  fireReady(doc);
  return { doc, chrome, ctx };
}

// ---- guard ----

test("popup dist exists", () => {
  if (!existsSync(DIST_POPUP)) {
    console.log("SKIP: dist/popup.js not built yet — run parent build first");
  }
  assert.ok(existsSync(DIST_POPUP), "dist/popup.js must exist (run build first)");
});

// ---- tests ----

test("checkbox starts disabled in HTML", () => {
  const html = readFileSync(resolve(import.meta.dirname, "../dist/popup.html"), "utf8");
  assert.match(html, /<input[^>]*id="enhance-toggle"[^>]*disabled/);
});

test("read success enables checkbox with correct state", async () => {
  const { doc } = freshSetup({
    readResponse: { ok: true, enabled: true, pageKey: "pk-1" },
  });
  await flush();
  const cb = doc.getElementById("enhance-toggle");
  assert.equal(cb.disabled, false);
  assert.equal(cb.checked, true);
});

test("read success enabled=false leaves unchecked", async () => {
  const { doc } = freshSetup({
    readResponse: { ok: true, enabled: false, pageKey: "pk-2" },
  });
  await flush();
  const cb = doc.getElementById("enhance-toggle");
  assert.equal(cb.disabled, false);
  assert.equal(cb.checked, false);
});

test("read failure keeps checkbox disabled with error", async () => {
  const { doc } = freshSetup({ reject: true });
  await flush();
  const cb = doc.getElementById("enhance-toggle");
  const status = doc.getElementById("status");
  assert.equal(cb.disabled, true);
  assert.equal(cb.checked, false);
  assert.equal(status.hidden, false);
  assert.ok(status.textContent.length > 0);
});

test("read ok:false treated as failure", async () => {
  const { doc } = freshSetup({
    readResponse: { ok: false },
  });
  await flush();
  const cb = doc.getElementById("enhance-toggle");
  const status = doc.getElementById("status");
  assert.equal(cb.disabled, true);
  assert.equal(status.hidden, false);
});

test("no active tab shows error", async () => {
  const { doc } = freshSetup({ activeTab: null });
  await flush();
  const cb = doc.getElementById("enhance-toggle");
  const status = doc.getElementById("status");
  assert.equal(cb.disabled, true);
  assert.equal(status.hidden, false);
});

test("toggle sends correct set message", async () => {
  const { doc, chrome } = freshSetup({
    readResponse: { ok: true, enabled: false, pageKey: "pk-3" },
  });
  await flush();

  // Prepare set response for the toggle
  chrome._nextResponse = { ok: true, enabled: true, pageKey: "pk-3" };
  chrome._reject = false;

  const cb = doc.getElementById("enhance-toggle");
  cb.checked = true;
  cb.dispatchEvent(new Event("change"));
  await flush();

  const setCall = chrome._calls.find(c => c.method === "sendMessage" && c.message.action === "set");
  assert.ok(setCall, "set message must be sent");
  assert.equal(setCall.tabId, 42);
  assert.equal(setCall.message.type, "page-enhancement");
  assert.equal(setCall.message.enabled, true);
  assert.equal(setCall.message.pageKey, "pk-3");
});

test("set success updates checkbox", async () => {
  const { doc, chrome } = freshSetup({
    readResponse: { ok: true, enabled: false, pageKey: "pk-4" },
  });
  await flush();

  chrome._nextResponse = { ok: true, enabled: true, pageKey: "pk-4" };
  chrome._reject = false;

  const cb = doc.getElementById("enhance-toggle");
  cb.checked = true;
  cb.dispatchEvent(new Event("change"));
  await flush();

  assert.equal(cb.disabled, false);
  assert.equal(cb.checked, true);
});

test("set failure reverts checkbox and shows error", async () => {
  const { doc, chrome } = freshSetup({
    readResponse: { ok: true, enabled: false, pageKey: "pk-5" },
  });
  await flush();

  chrome._nextResponse = null;
  chrome._reject = true;

  const cb = doc.getElementById("enhance-toggle");
  const status = doc.getElementById("status");
  cb.checked = true;
  cb.dispatchEvent(new Event("change"));
  await flush();

  assert.equal(cb.checked, false, "must revert to original unchecked");
  assert.equal(status.hidden, false, "error must be visible");
  assert.ok(status.textContent.length > 0);
});

test("set ok:false reverts checkbox", async () => {
  const { doc, chrome } = freshSetup({
    readResponse: { ok: true, enabled: true, pageKey: "pk-6" },
  });
  await flush();

  chrome._nextResponse = { ok: false };
  chrome._reject = false;

  const cb = doc.getElementById("enhance-toggle");
  const status = doc.getElementById("status");
  assert.equal(cb.checked, true);

  cb.checked = false;
  cb.dispatchEvent(new Event("change"));
  await flush();

  assert.equal(cb.checked, true, "must revert to original checked");
  assert.equal(status.hidden, false);
});

test("read sends correct message shape", async () => {
  const { chrome } = freshSetup({
    readResponse: { ok: true, enabled: false, pageKey: "pk-7" },
  });
  await flush();

  const queryCall = chrome._calls.find(c => c.method === "query");
  assert.ok(queryCall, "query must be called");
  assert.equal(queryCall.args.active, true);
  assert.equal(queryCall.args.currentWindow, true);

  const readCall = chrome._calls.find(c => c.method === "sendMessage" && c.message.action === "read");
  assert.ok(readCall, "read message must be sent");
  assert.equal(readCall.tabId, 42);
  assert.equal(readCall.message.type, "page-enhancement");
  assert.equal(readCall.message.action, "read");
});

test('malformed read success is not accepted as a valid page state',async()=>{
 for(const readResponse of [{ok:true},{ok:true,enabled:'true',pageKey:'p'},{ok:true,enabled:true,pageKey:''}]){
  const {doc}=freshSetup({readResponse});await flush();
  assert.equal(doc.getElementById('enhance-toggle').disabled,true);
 }
});
test('toggle remains bound to originally read tab and uses acknowledged state',async()=>{
 const {doc,chrome}=freshSetup({readResponse:{ok:true,enabled:true,pageKey:'p'}});await flush();
 chrome._activeTab={id:99};chrome._nextResponse={ok:true,enabled:true,pageKey:'p'};
 const cb=doc.getElementById('enhance-toggle');cb.checked=false;cb.dispatchEvent(new Event('change'));
 assert.equal(cb.disabled,true);await flush();
 assert.equal(chrome._calls.find(c=>c.message?.action==='set').tabId,42);
 assert.equal(cb.checked,true);
});
