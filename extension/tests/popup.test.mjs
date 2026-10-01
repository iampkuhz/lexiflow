import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync, existsSync, statSync } from "node:fs";
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
          hidden: id === "restore-status" || id === "restore-confirmation",
          textContent: "",
          dataset: {},
          open: false,
          focus() { this.focused = true; },
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
    runtime: { lastError: null, sendMessage(message) {
      chromeObj._calls.push({ method: "runtime.sendMessage", message: { ...message } });
      if (message.type === "runtime-status") {
        if (behaviour.statusReject) return Promise.reject(new Error("status unavailable"));
        return Promise.resolve(behaviour.statusResponse ?? {ok:true,status:{softwareVersion:"1.2.3",apiContract:"caption-hints.v1",mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null}});
      }
      return behaviour.preferenceResponse !== undefined ? Promise.resolve(behaviour.preferenceResponse) : Promise.resolve({ ok: true, entryKeys: [] });
    } },
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
  assert.match(html, /<input[^>]*id="enhance-toggle"[^>]*role="switch"[^>]*disabled/);
  assert.match(html, /summary>提示偏好 · 仅本机</);
  assert.match(html, /点击中文可不再提示；仅本机、对应词库版本有效/);
  assert.match(readFileSync(resolve(import.meta.dirname, "../dist/popup.css"), "utf8"), /light-dark\(/);
});

test("popup branding and manifest icon assets are packaged without added permissions", () => {
  const html = readFileSync(resolve(import.meta.dirname, "../dist/popup.html"), "utf8");
  const manifest = JSON.parse(readFileSync(resolve(import.meta.dirname, "../dist/manifest.json"), "utf8"));
  assert.match(html, /src="assets\/logo\.svg"/);
  assert.match(html, /summary>提示偏好 · 仅本机</);
  for (const size of [16, 32, 48, 128]) {
    const icon = readFileSync(resolve(import.meta.dirname, `../dist/assets/icon-${size}.png`));
    assert.equal(icon.subarray(1, 4).toString(), "PNG");
    assert.equal(icon.readUInt32BE(16), size);
    assert.equal(icon.readUInt32BE(20), size);
  }
  assert.deepEqual(manifest.permissions, ["storage"]);
  assert.deepEqual(Object.keys(manifest.icons).map(Number).sort((a, b) => a - b), [16, 32, 48, 128]);
  assert.deepEqual(manifest.action.default_icon, {"16":"assets/icon-16.png","32":"assets/icon-32.png"});
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
  const status = doc.getElementById("page-status");
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
  const status = doc.getElementById("page-status");
  assert.equal(cb.disabled, true);
  assert.equal(status.hidden, false);
});

test("no active tab shows error", async () => {
  const { doc } = freshSetup({ activeTab: null });
  await flush();
  const cb = doc.getElementById("enhance-toggle");
  const status = doc.getElementById("page-status");
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
  const status = doc.getElementById("page-status");
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
  const status = doc.getElementById("page-status");
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

test('set receipt with another pageKey is rejected and rolls back',async()=>{
 const {doc,chrome}=freshSetup({readResponse:{ok:true,enabled:false,pageKey:'original'}});await flush();
 chrome._nextResponse={ok:true,enabled:true,pageKey:'different'};
 const cb=doc.getElementById('enhance-toggle');cb.checked=true;cb.dispatchEvent(new Event('change'));await flush();
 assert.equal(cb.checked,false);assert.equal(doc.getElementById('page-status').dataset.kind,'error');
});

test('set state follows a valid same-pageKey acknowledgement even if it differs from desired',async()=>{
 const {doc,chrome}=freshSetup({readResponse:{ok:true,enabled:false,pageKey:'ack-page'}});await flush();
 chrome._nextResponse={ok:true,enabled:false,pageKey:'ack-page'};
 const cb=doc.getElementById('enhance-toggle');cb.checked=true;cb.dispatchEvent(new Event('change'));await flush();
 assert.equal(cb.checked,false);assert.equal(doc.getElementById('page-status').textContent,'已关闭 · 保留英文');
});

test('restore requires explicit confirmation and reports only empty success receipt',async()=>{
 const {doc,chrome}=freshSetup({readResponse:{ok:true,enabled:false,pageKey:'restore-page'}});await flush();
 const details=doc.getElementById('preferences');details.open=true;
 doc.getElementById('restore-start').dispatchEvent(new Event('click'));
 assert.equal(doc.getElementById('restore-confirmation').hidden,false);
 doc.getElementById('restore-cancel').dispatchEvent(new Event('click'));await flush();
 assert.equal(doc.getElementById('restore-confirmation').hidden,true);assert.equal(chrome._calls.some(call=>call.method==='runtime.sendMessage'&&call.message.type==='local-preferences'),false);
 doc.getElementById('restore-start').dispatchEvent(new Event('click'));
 doc.getElementById('restore-confirm').dispatchEvent(new Event('click'));
 assert.equal(doc.getElementById('restore-confirm').disabled,true);
 assert.equal(doc.getElementById('restore-cancel').disabled,true);
 assert.equal(doc.getElementById('restore-start').disabled,true);
 doc.getElementById('restore-confirm').dispatchEvent(new Event('click'));
 await flush();assert.equal(doc.getElementById('restore-status').dataset.kind,'success');
 assert.equal(doc.getElementById('restore-status').textContent,'已恢复全部提示偏好。');
 assert.equal(details.open,true,'success feedback remains visible inside the expanded details');
 assert.deepEqual(chrome._calls.find(call=>call.method==='runtime.sendMessage'&&call.message.type==='local-preferences').message,{type:'local-preferences',action:'restore-all'});
 assert.equal(chrome._calls.filter(call=>call.method==='runtime.sendMessage'&&call.message.type==='local-preferences').length,1,'duplicate confirm cannot dispatch twice');
});

test('restore malformed, non-empty, or failed receipt never claims success',async()=>{
 for(const preferenceResponse of [null,{ok:true},{ok:true,entryKeys:['x']},{ok:true,entryKeys:['x',3]},{ok:false,reason:'storage'}]){
  const {doc}=freshSetup({readResponse:{ok:true,enabled:false,pageKey:'restore-fail'},preferenceResponse});await flush();
  doc.getElementById('restore-start').dispatchEvent(new Event('click'));
  doc.getElementById('restore-confirm').dispatchEvent(new Event('click'));await flush();
  assert.equal(doc.getElementById('restore-status').dataset.kind,'error');
 }
});

test('restore failure is independent from later successful page toggle',async()=>{
 const {doc,chrome}=freshSetup({readResponse:{ok:true,enabled:false,pageKey:'independent'}});await flush();
 chrome.runtime.sendMessage=async()=>({ok:false,reason:'storage'});
 doc.getElementById('restore-start').dispatchEvent(new Event('click'));
 doc.getElementById('restore-confirm').dispatchEvent(new Event('click'));await flush();
 assert.equal(doc.getElementById('restore-status').dataset.kind,'error');
 chrome._nextResponse={ok:true,enabled:true,pageKey:'independent'};
 const toggle=doc.getElementById('enhance-toggle');toggle.checked=true;toggle.dispatchEvent(new Event('change'));await flush();
 assert.equal(doc.getElementById('restore-status').dataset.kind,'error');
 assert.equal(doc.getElementById('page-status').textContent,'已开启 · 英文优先');
});

test('service status renders formal readiness, degraded warmup, demo, missing data, dependency and schema states',async()=>{
 const cases=[
  [{mode:'formal',ready:true,reason:'OK',datasetVersion:7},'正式就绪。',false],
  [{mode:'formal',ready:true,reason:'PREWARM_DEGRADED',datasetVersion:7},'正式就绪 · 预热降级，服务可用。',false],
  [{mode:'demo',ready:false,reason:'DEMO_MODE',datasetVersion:null},'演示模式 · 非正式就绪',false],
  [{mode:'formal',ready:false,reason:'NO_PUBLISHED_DATA',datasetVersion:0},'尚无已发布资料，请检查资料初始化与发布状态。',false],
  [{mode:'formal',ready:false,reason:'DEPENDENCY_UNAVAILABLE',datasetVersion:null},'服务依赖不可用，请检查后端依赖服务。',false],
  [{mode:'formal',ready:false,reason:'SCHEMA_MISMATCH',datasetVersion:null},'资料结构不匹配，请使用匹配的服务与资料。',false]
 ];
 for(const [state,expected] of cases){
  const value={softwareVersion:'1.2.3',apiContract:'caption-hints.v1',...state};
  const {doc}=freshSetup({statusResponse:{ok:true,status:value}});await flush();
  assert.equal(doc.getElementById('service-status').textContent,expected);
  assert.equal(doc.getElementById('service-identity').hidden,false);
  assert.match(doc.getElementById('service-identity').textContent,/软件 1\.2\.3 · 协议 caption-hints\.v1/u);
  assert.ok(doc.getElementById('service-identity').textContent.includes(state.datasetVersion===null?'未知':String(state.datasetVersion)));
 }
});

test('protocol mismatch and unreachable states are fixed safe messages and hide untrusted identity',async()=>{
 const mismatch=freshSetup({statusResponse:{ok:false,reason:'protocol-mismatch'}});await flush();
 assert.equal(mismatch.doc.getElementById('service-status').textContent,'协议不匹配，请使用匹配的扩展与服务版本。');
 assert.equal(mismatch.doc.getElementById('service-identity').hidden,true);
 const unreachable=freshSetup({statusReject:true,readResponse:{ok:true,enabled:false,pageKey:'page'}});await flush();
 assert.equal(unreachable.doc.getElementById('service-status').textContent,'无法连接服务，请检查后端是否启动。');
 assert.equal(unreachable.doc.getElementById('service-identity').hidden,true);
 assert.equal(unreachable.doc.getElementById('enhance-toggle').disabled,false,'status failure does not disable page controls');
 assert.equal(unreachable.doc.getElementById('enhance-toggle').checked,false);
});

test('status lookup failure leaves page enhancement and local preference controls usable',async()=>{
 const {doc,chrome}=freshSetup({statusReject:true,readResponse:{ok:true,enabled:false,pageKey:'independent-page'}});await flush();
 const toggle=doc.getElementById('enhance-toggle');assert.equal(toggle.disabled,false);
 chrome._nextResponse={ok:true,enabled:true,pageKey:'independent-page'};
 toggle.checked=true;toggle.dispatchEvent(new Event('change'));await flush();
 assert.equal(toggle.checked,true);
 const details=doc.getElementById('preferences');details.open=true;
 doc.getElementById('restore-start').dispatchEvent(new Event('click'));
 doc.getElementById('restore-confirm').dispatchEvent(new Event('click'));await flush();
 assert.equal(doc.getElementById('restore-status').dataset.kind,'success');
 assert.equal(chrome._calls.some(call=>call.message?.type==='local-preferences'&&call.message.action==='restore-all'),true);
});
