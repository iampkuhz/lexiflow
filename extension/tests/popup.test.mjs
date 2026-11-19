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
          attributes: {},
          setAttribute(name, value) { this.attributes[name] = value; },
          getAttribute(name) { return this.attributes[name] ?? null; },
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
      create(options) { chromeObj._calls.push({ method: "create", options: { ...options } }); return Promise.resolve({ id: 100 }); },
    },
    runtime: { lastError: null, getURL(path) { return `chrome-extension://synthetic/${path}`; }, getManifest() { return { version_name: "1.2.3" }; }, sendMessage(message) {
      chromeObj._calls.push({ method: "runtime.sendMessage", message: { ...message } });
      if (message.type === "runtime-status") {
        if (behaviour.statusReject) return Promise.reject(new Error("status unavailable"));
        return Promise.resolve(behaviour.statusResponse ?? {ok:true,status:{softwareVersion:"1.2.3",apiContract:"caption-hints.v2",mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null,publishedDataset:null}});
      }
      return behaviour.preferenceResponse !== undefined ? Promise.resolve(behaviour.preferenceResponse) : Promise.resolve({ ok: true, commonWordCutoff: 1500, hiddenCount: 2 });
    } },
  };
  return chromeObj;
}

// ---- helpers ----

function makeContext(doc, chrome) {
  const ctx = {
    document: doc,
    chrome,
    navigator: { clipboard: { async writeText(text) { ctx.copiedText = text; } } },
    URL,
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
  assert.match(html, /id="cutoff-summary"/);
  assert.doesNotMatch(html, /id="common-word-cutoff"/);
  assert.match(html, /id="open-manage"/);
  assert.match(readFileSync(resolve(import.meta.dirname, "../dist/popup.css"), "utf8"), /light-dark\(/);
});

test("popup branding and manifest icon assets are packaged without added permissions", () => {
  const html = readFileSync(resolve(import.meta.dirname, "../dist/popup.html"), "utf8");
  const manifest = JSON.parse(readFileSync(resolve(import.meta.dirname, "../dist/manifest.json"), "utf8"));
  assert.match(html, /src="assets\/logo\.svg"/);
  assert.match(html, /管理提示与词库/);
  for (const size of [16, 32, 48, 128]) {
    const icon = readFileSync(resolve(import.meta.dirname, `../dist/assets/icon-${size}.png`));
    assert.equal(icon.subarray(1, 4).toString(), "PNG");
    assert.equal(icon.readUInt32BE(16), size);
    assert.equal(icon.readUInt32BE(20), size);
  }
  assert.deepEqual(manifest.permissions, []);
  assert.deepEqual(Object.keys(manifest.icons).map(Number).sort((a, b) => a - b), [16, 32, 48, 128]);
  assert.deepEqual(manifest.action.default_icon, {"16":"assets/icon-16.png","32":"assets/icon-32.png"});
  assert.equal(manifest.options_page, "manage.html");
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

test("popup renders read-only preference summary without leaking hidden entries or writing cutoff",async()=>{
 const {doc,chrome}=freshSetup({preferenceResponse:{ok:true,commonWordCutoff:1000,hiddenCount:3}});await flush();
 assert.equal(doc.getElementById("cutoff-summary").textContent,"Top 1000");
 assert.equal(doc.getElementById("hidden-summary").textContent,"3 条");
 assert.equal(chrome._calls.some(x=>x.message?.action==="setCutoff"),false);
 assert.equal(chrome._calls.some(x=>x.message?.type==="local-preferences"&&x.message.action==="read"),true);
});
test("unavailable preference summary does not masquerade as zero",async()=>{
 const {doc}=freshSetup({preferenceResponse:{ok:false,reason:"network"}});await flush();
 assert.equal(doc.getElementById("cutoff-summary").textContent,"不可用");
 assert.equal(doc.getElementById("hidden-summary").textContent,"不可用");
});

test("management button opens only the packaged local management page",async()=>{
 const {doc,chrome}=freshSetup();await flush();doc.getElementById("open-manage").dispatchEvent(new Event("click"));await flush();
 const call=chrome._calls.find(x=>x.method==="create");assert.deepEqual(call.options,{url:"chrome-extension://synthetic/manage.html"});
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

test('service status renders formal readiness, degraded warmup, demo, missing data, dependency and schema states',async()=>{
 const dataset={releaseId:'release-1',preparationId:'prep-1',ruleId:'rules-1',sqlVersion:'4',sha256:'a'.repeat(64)};
 const cases=[
  [{mode:'formal',ready:true,reason:'OK',datasetVersion:7,publishedDataset:dataset},'正式就绪。',false],
  [{mode:'formal',ready:true,reason:'PREWARM_DEGRADED',datasetVersion:7,publishedDataset:dataset},'正式就绪 · 预热降级，服务可用。',false],
  [{mode:'formal',ready:false,reason:'INITIALIZING',datasetVersion:null,publishedDataset:null},'服务正在初始化发布资料…',false],
  [{mode:'demo',ready:false,reason:'DEMO_MODE',datasetVersion:null,publishedDataset:null},'演示模式 · 非正式就绪',false],
  [{mode:'formal',ready:false,reason:'NO_PUBLISHED_DATA',datasetVersion:0,publishedDataset:null},'尚无已发布资料，请检查资料初始化与发布状态。',false],
  [{mode:'formal',ready:false,reason:'DEPENDENCY_UNAVAILABLE',datasetVersion:null,publishedDataset:null},'服务依赖不可用，请检查后端依赖服务。',false],
  [{mode:'formal',ready:false,reason:'IDENTITY_MISMATCH',datasetVersion:7,publishedDataset:null},'服务发行资料身份校验失败，请检查匹配的发行包。',false],
  [{mode:'formal',ready:false,reason:'SCHEMA_MISMATCH',datasetVersion:null,publishedDataset:null},'资料结构不匹配，请使用匹配的服务与资料。',false]
 ];
 for(const [state,expected] of cases){
  const value={softwareVersion:'1.2.3',apiContract:'caption-hints.v2',...state};
  const {doc}=freshSetup({statusResponse:{ok:true,status:value}});await flush();
  assert.equal(doc.getElementById('service-status').textContent,expected);
  const detailsVisible=state.mode==='formal'&&state.ready&&state.publishedDataset!==null;
  assert.equal(doc.getElementById('service-identity').hidden,!detailsVisible);
  assert.equal(doc.getElementById('service-diagnostics').hidden,true,'details stay closed for every runtime state');
  assert.equal(doc.getElementById('service-status').hidden, state.mode==='formal'&&state.ready&&state.reason!=='PREWARM_DEGRADED');
  assert.equal(doc.getElementById('service-identity').textContent,'软件 1.2.3');
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

test('a runtime software version different from the extension build is rejected visibly',async()=>{
 const {doc}=freshSetup({statusResponse:{ok:false,reason:'software-version'}});await flush();
 assert.equal(doc.getElementById('service-status').textContent,'软件版本不匹配，请安装与服务对应的扩展。');
});

test('an unverified or incompatible dataset binding is clearly rejected',async()=>{
 const missing=freshSetup({statusResponse:{ok:false,reason:'binding-missing'}});await flush();
 assert.match(missing.doc.getElementById('service-status').textContent,/缺少发行资料绑定/u);
 const mismatch=freshSetup({statusResponse:{ok:false,reason:'dataset-identity'}});await flush();
 assert.match(mismatch.doc.getElementById('service-status').textContent,/资料身份不匹配/u);
 const invalid=freshSetup({statusResponse:{ok:false,reason:'invalid-response'}});await flush();
 assert.match(invalid.doc.getElementById('service-status').textContent,/服务\/API合同不兼容/u);
});

test('status lookup failure leaves page enhancement usable and displays configured target as disconnected',async()=>{
 const {doc,chrome}=freshSetup({statusReject:true,readResponse:{ok:true,enabled:false,pageKey:'independent-page'}});await flush();
 const toggle=doc.getElementById('enhance-toggle');assert.equal(toggle.disabled,false);
 chrome._nextResponse={ok:true,enabled:true,pageKey:'independent-page'};
 toggle.checked=true;toggle.dispatchEvent(new Event('change'));await flush();
 assert.equal(toggle.checked,true);
 assert.match(doc.getElementById('backend-target').textContent,/^○ 未连接 127\.0\.0\.1:\d+$/u);
 assert.equal(chrome._calls.some(call=>call.message?.type==='local-preferences'&&call.message.action==='setCutoff'),false);
});

 test("non-video page explains navigation instead of refresh", async () => {
  const {doc} = freshSetup({readResponse:{ok:false,reason:"not-video-page"}});
  await flush();
  assert.match(doc.getElementById("page-status").textContent, /打开 YouTube 视频即可使用/u);
  assert.equal(doc.getElementById("enhance-toggle").disabled, true);
 });
 test("disabled controls never use a perpetual waiting cursor", () => {
  const css=readFileSync(resolve(import.meta.dirname,"../src/popup.css"),"utf8");
  assert.doesNotMatch(css, /cursor:\s*wait/);
 });
 test("hung page message ends waiting and ignores late reply", async () => {
  const doc=createDocument(), chrome=createChrome();
  let late;
  chrome.tabs.sendMessage=()=>new Promise(resolve=>{late=resolve;});
  const ctx=makeContext(doc,chrome), timers=[];
  ctx.setTimeout=(fn)=>{timers.push(fn); return timers.length;};
  ctx.clearTimeout=()=>{};
  loadPopup(ctx); fireReady(doc); await flush();
  timers.forEach(fn=>fn()); await flush();
  assert.match(doc.getElementById("page-status").textContent,/打开 YouTube 视频即可使用/u);
  late({ok:true,enabled:true,pageKey:"late"}); await flush();
  assert.equal(doc.getElementById("enhance-toggle").disabled,true);
 });


test('healthy popup puts primary action immediately after brand and only useful preferences in summary',()=>{
 const html=readFileSync(resolve(import.meta.dirname,'../src/popup.html'),'utf8');
 assert.match(html,/<main>\s*<section id="page-section"/u);
 assert.ok(html.indexOf('id="enhance-toggle"')<html.indexOf('id="summary-section"'));
 assert.ok(html.indexOf('id="summary-section"')<html.indexOf('id="service-section"'));
 assert.doesNotMatch(html,/服务状态<|当前页<|手动添加|未开放|<select/u);
 assert.match(html,/id="diagnostics-toggle"[^>]*aria-label="连接与版本详情"[^>]*aria-expanded="false"[^>]*aria-controls="service-diagnostics"/u);
});
test('healthy service connection is one line and normal page state is conveyed by the switch',async()=>{
 const status={softwareVersion:'1.2.3',apiContract:'caption-hints.v2',mode:'formal',ready:true,reason:'OK',datasetVersion:7,publishedDataset:{releaseId:'r',preparationId:'p',ruleId:'rules',sqlVersion:'4',sha256:'a'.repeat(64)}};
 for(const enabled of [true,false]){
  const {doc}=freshSetup({statusResponse:{ok:true,status},readResponse:{ok:true,enabled,pageKey:'p'}});await flush();
  assert.match(doc.getElementById('backend-target').textContent,/^● 已连接 127\.0\.0\.1:\d+$/u);
  assert.equal(doc.getElementById('service-status').hidden,true);
  assert.equal(doc.getElementById('page-status').hidden,true);
  assert.equal(doc.getElementById('enhance-toggle').checked,enabled);
  assert.equal(doc.getElementById('enhance-toggle').disabled,false);
 }
});
test('details toggle and Escape synchronize expanded state and restore focus without new requests',async()=>{
 const {doc,chrome}=freshSetup({statusResponse:{ok:false,reason:'dataset-identity'}});await flush();
 const button=doc.getElementById('diagnostics-toggle'),panel=doc.getElementById('service-diagnostics'),calls=chrome._calls.length;
 assert.equal(button.getAttribute('aria-expanded'),'false');assert.equal(panel.hidden,true);
 button.dispatchEvent(new Event('click'));
 assert.equal(button.getAttribute('aria-expanded'),'true');assert.equal(panel.hidden,false);
 assert.match(doc.getElementById('diagnostic-target').textContent,/^127\.0\.0\.1:\d+$/u);
 assert.equal(doc.getElementById('service-status').hidden,false,'errors remain visible without expanding');
 let prevented=false;doc.dispatchEvent({type:'keydown',key:'Escape',preventDefault(){prevented=true;}});
 assert.equal(panel.hidden,true);assert.equal(button.getAttribute('aria-expanded'),'false');assert.equal(button.focused,true);assert.equal(prevented,true);
 button.dispatchEvent(new Event('click'));button.dispatchEvent(new Event('click'));assert.equal(panel.hidden,true);
 assert.equal(chrome._calls.length,calls,'details do not fetch or poll');
});
test('preference failures explain recovery without inventing counts',async()=>{
 const {doc}=freshSetup({preferenceResponse:{ok:false}});await flush();
 assert.equal(doc.getElementById('preference-status').hidden,false);
 assert.match(doc.getElementById('preference-status').textContent,/重新打开弹窗/u);
 assert.equal(doc.getElementById('hidden-summary').textContent,'不可用');
});

test('popup explicitly inherits its readable font instead of native extension body scaling',()=>{
 const css=readFileSync(resolve(import.meta.dirname,'../src/popup.css'),'utf8');
 assert.match(css,/body\s*\{[^}]*font:inherit/u);
 assert.match(css,/font:\s*13px\/1\.45/u);
 assert.doesNotMatch(css,/body[^}]*overflow:\s*hidden/u);
});

const diagnosticRuntime = {
 softwareVersion:'2.4.1-SNAPSHOT.20261108.gf3191b5.dirty.123456789abc',
 apiContract:'caption-hints.v2',mode:'formal',ready:true,reason:'OK',datasetVersion:7,
 publishedDataset:{releaseId:'r',preparationId:'p',ruleId:'rules',sqlVersion:'4',sha256:'a'.repeat(64)}
};
test('compact version labels a real snapshot without dumping technical identities into the UI',async()=>{
 const {doc}=freshSetup({statusResponse:{ok:true,status:diagnosticRuntime}});await flush();
 assert.equal(doc.getElementById('service-identity').textContent,'软件 2.4.1 · 开发版');
 assert.equal(doc.getElementById('service-identity').hidden,false);
 assert.doesNotMatch(doc.getElementById('service-identity').textContent,/SNAPSHOT|123456789abc|caption-hints|SHA-256/u);
});
test('copy preserves complete diagnostic identities but excludes unrelated private payloads',async()=>{
 const status={...diagnosticRuntime,token:'fixture-secret',captions:'fixture-caption',
  publishedDataset:{...diagnosticRuntime.publishedDataset,history:'fixture-history'}};
 const {doc,ctx,chrome}=freshSetup({statusResponse:{ok:true,status}});await flush();
 assert.equal(ctx.copiedText,undefined,'never copy without explicit action');
 const calls=chrome._calls.length;
 doc.getElementById('copy-diagnostics').dispatchEvent(new Event('click'));await flush();
 const copied=JSON.parse(ctx.copiedText);
 assert.equal(copied.runtime.softwareVersion,diagnosticRuntime.softwareVersion);
 assert.equal(copied.extensionVersion,'1.2.3');
 assert.deepEqual(copied.runtime.publishedDataset,diagnosticRuntime.publishedDataset);
 assert.equal(copied.runtime.apiContract,'caption-hints.v2');
 assert.equal(copied.runtime.datasetVersion,7);
 assert.match(copied.backend,/^http:\/\/127\.0\.0\.1:\d+$/u);
 assert.doesNotMatch(ctx.copiedText,/fixture-secret|fixture-caption|fixture-history|commonWordCutoff|pageKey/u);
 assert.equal(doc.getElementById('copy-status').textContent,'诊断信息已复制');
 assert.equal(doc.getElementById('copy-status').hidden,false);
 assert.equal(doc.getElementById('copy-diagnostics').disabled,false);
 assert.equal(chrome._calls.length,calls,'copy is local, with no new network/messages');
});
test('copy failure is visible and retry works without hiding the real connection error',async()=>{
 const {doc,ctx}=freshSetup({statusReject:true});await flush();
 ctx.navigator.clipboard.writeText=async()=>{throw Error('fixture denied')};
 doc.getElementById('copy-diagnostics').dispatchEvent(new Event('click'));await flush();
 assert.equal(doc.getElementById('copy-status').dataset.kind,'error');
 assert.equal(doc.getElementById('copy-status').textContent,'复制失败，请重试。');
 assert.equal(doc.getElementById('copy-diagnostics').disabled,false);
 assert.equal(doc.getElementById('service-status').hidden,false);
 assert.match(doc.getElementById('service-status').textContent,/无法连接服务/u);
 ctx.navigator.clipboard.writeText=async text=>{ctx.copiedText=text};
 doc.getElementById('copy-diagnostics').dispatchEvent(new Event('click'));await flush();
 assert.equal(JSON.parse(ctx.copiedText).runtime,null);
 assert.match(JSON.parse(ctx.copiedText).status,/无法连接服务/u);
 assert.equal(doc.getElementById('copy-status').dataset.kind,'success');
});
test('missing clipboard API reports failure and a hung clipboard operation ends waiting',async()=>{
 const missing=freshSetup();await flush();missing.ctx.navigator={};
 missing.doc.getElementById('copy-diagnostics').dispatchEvent(new Event('click'));await flush();
 assert.equal(missing.doc.getElementById('copy-status').dataset.kind,'error');
 const {doc,ctx}=freshSetup();await flush();
 const timers=[];let late;
 ctx.setTimeout=fn=>{timers.push(fn);return timers.length};ctx.clearTimeout=()=>{};
 ctx.navigator.clipboard.writeText=()=>new Promise(resolve=>{late=resolve});
 doc.getElementById('copy-diagnostics').dispatchEvent(new Event('click'));await flush();
 assert.equal(doc.getElementById('copy-diagnostics').disabled,true);
 timers.forEach(fn=>fn());await flush();
 assert.equal(doc.getElementById('copy-diagnostics').disabled,false);
 assert.equal(doc.getElementById('copy-status').dataset.kind,'error');
 late();await flush();
 assert.equal(doc.getElementById('copy-status').dataset.kind,'error','late completion cannot claim success');
});
