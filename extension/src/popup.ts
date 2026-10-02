interface PageReply { ok: true; enabled: boolean; pageKey: string }
type PageResponse = PageReply | { ok: false; reason?: string };
type PreferenceResponse = { ok: true; entryKeys: string[] } | { ok: false; reason?: string };
type RuntimeReply = { ok: true; status: { softwareVersion: string; apiContract: string; mode: string; ready: boolean; reason: string; datasetVersion: number | null } } | { ok: false; reason: string };

// 消息通道异常时也必须结束等待；迟到结果不再改变弹窗状态。
function bounded<T>(operation: Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("message timeout")), 5000);
    operation.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}
let pageKey: string | null = null;
let tabId: number | undefined;
let restoreBusy = false;
function el<T extends HTMLElement>(id: string): T { return document.getElementById(id) as T; }
function pageMessage(text: string, kind: "error" | "success" | "pending" = "error"): void {
  const status = el<HTMLDivElement>("page-status"); status.textContent = text; status.dataset.kind = kind;
}
function restoreMessage(text: string, kind: "error" | "success"): void {
  const status = el<HTMLDivElement>("restore-status"); status.textContent = text; status.dataset.kind = kind; status.hidden = false;
}
function renderPageState(enabled: boolean): void {
  const status = el<HTMLDivElement>("page-status");
  status.textContent = enabled ? "已开启 · 英文优先" : "已关闭 · 保留英文";
  status.dataset.kind = "success";
}
async function readServiceStatus(): Promise<void> {
  const status = el<HTMLDivElement>("service-status"), identity = el<HTMLDivElement>("service-identity");
  status.textContent = "正在查询服务状态…";
  try {
    const reply = await bounded(chrome.runtime.sendMessage({ type: "runtime-status" })) as RuntimeReply | undefined;
    if (!reply || reply.ok !== true) {
      if (reply?.reason === "protocol-mismatch") {
        status.textContent = "协议不匹配，请使用匹配的扩展与服务版本。"; status.dataset.kind = "error"; identity.hidden = true; return;
      }
      throw new Error("status unavailable");
    }
    const value = reply.status;
    identity.textContent = `软件 ${value.softwareVersion} · 协议 ${value.apiContract} · 已发布资料版本 ${value.datasetVersion === null ? "未知" : value.datasetVersion}`;
    identity.hidden = false;
    if (value.mode === "demo") { status.textContent = "演示模式 · 非正式就绪"; status.dataset.kind = "error"; }
    else if (value.mode === "invalid" || value.reason === "SCHEMA_MISMATCH") { status.textContent = "资料结构不匹配，请使用匹配的服务与资料。"; status.dataset.kind = "error"; }
    else if (value.reason === "NO_PUBLISHED_DATA") { status.textContent = "尚无已发布资料，请检查资料初始化与发布状态。"; status.dataset.kind = "error"; }
    else if (value.reason === "DEPENDENCY_UNAVAILABLE") { status.textContent = "服务依赖不可用，请检查后端依赖服务。"; status.dataset.kind = "error"; }
    else if (value.ready) { status.textContent = value.reason === "PREWARM_DEGRADED" ? "正式就绪 · 预热降级，服务可用。" : "正式就绪。"; status.dataset.kind = "success"; }
    else { status.textContent = "资料初始化未完成。"; status.dataset.kind = "error"; }
  } catch {
    identity.hidden = true;
    status.textContent = "无法连接服务，请检查后端是否启动。";
    status.dataset.kind = "error";
  }
}
function validPageReply(value: unknown): value is PageReply {
  const result = value as PageReply | undefined;
  return !!result && result.ok === true && typeof result.enabled === "boolean" && typeof result.pageKey === "string" && result.pageKey.length > 0;
}
async function readPage(): Promise<void> {
  const toggle = el<HTMLInputElement>("enhance-toggle"); toggle.disabled = true;
  try {
    const [tab] = await bounded(chrome.tabs.query({ active: true, currentWindow: true }));
    if (typeof tab?.id !== "number") { pageMessage("没有可用标签页，请打开 YouTube 视频页面。"); return; }
    const response = await bounded(chrome.tabs.sendMessage(tab.id, { type: "page-enhancement", action: "read" })) as PageResponse | undefined;
    if (response?.ok === false && response.reason === "not-video-page") { pageMessage("请打开 YouTube 视频播放页后使用字幕增强。"); return; }
    if (!validPageReply(response)) { pageMessage("当前页面不可用，请刷新 YouTube 视频页面后重试。"); return; }
    tabId = tab.id; pageKey = response.pageKey; toggle.checked = response.enabled; toggle.disabled = false;
    renderPageState(response.enabled);
  } catch { pageMessage("当前页面不可用，请刷新 YouTube 视频页面后重试。"); }
}
async function setPage(): Promise<void> {
  const toggle = el<HTMLInputElement>("enhance-toggle"), desired = toggle.checked;
  if (tabId === undefined || pageKey === null) return;
  toggle.disabled = true;
  pageMessage("正在设置…", "pending");
  try {
    const response = await bounded(chrome.tabs.sendMessage(tabId, { type: "page-enhancement", action: "set", enabled: desired, pageKey })) as PageResponse | undefined;
    if (!validPageReply(response) || response.pageKey !== pageKey) throw new Error("invalid receipt");
    toggle.checked = response.enabled; toggle.disabled = false; renderPageState(response.enabled);
  } catch { toggle.checked = !desired; pageMessage("设置未成功，请重新打开弹窗后重试。"); }
}
async function restoreAll(): Promise<void> {
  if (restoreBusy) return;
  restoreBusy = true;
  const start = el<HTMLButtonElement>("restore-start"), confirm = el<HTMLButtonElement>("restore-confirm"), cancel = el<HTMLButtonElement>("restore-cancel");
  start.disabled = true; confirm.disabled = true; cancel.disabled = true;
  try {
    const response = await bounded(chrome.runtime.sendMessage({ type: "local-preferences", action: "restore-all" })) as PreferenceResponse | undefined;
    if (!response || response.ok !== true || !Array.isArray(response.entryKeys) || response.entryKeys.length !== 0) throw new Error("invalid receipt");
    restoreMessage("已恢复全部提示偏好。", "success");
    el<HTMLDivElement>("restore-confirmation").hidden = true;
    start.hidden = false;
    start.focus();
  } catch { restoreMessage("恢复失败；本机偏好未确认更改，请重试。", "error"); }
  finally { restoreBusy = false; start.disabled = false; confirm.disabled = false; cancel.disabled = false; }
}
document.addEventListener("DOMContentLoaded", () => {
  void readServiceStatus();
  el<HTMLInputElement>("enhance-toggle").addEventListener("change", () => { void setPage(); });
  el<HTMLButtonElement>("restore-start").addEventListener("click", () => {
    if (restoreBusy) return;
    el<HTMLDivElement>("restore-confirmation").hidden = false;
    el<HTMLButtonElement>("restore-start").hidden = true;
    el<HTMLDivElement>("restore-status").hidden = true;
    el<HTMLButtonElement>("restore-confirm").focus();
  });
  el<HTMLButtonElement>("restore-confirm").addEventListener("click", () => { void restoreAll(); });
  el<HTMLButtonElement>("restore-cancel").addEventListener("click", () => {
    if (restoreBusy) return;
    el<HTMLDivElement>("restore-confirmation").hidden = true;
    el<HTMLButtonElement>("restore-start").hidden = false;
    el<HTMLButtonElement>("restore-start").focus();
  });
  void readPage();
});
