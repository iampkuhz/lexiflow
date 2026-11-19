import type { RuntimeStatus } from "./runtime-status";
import { API_URL } from "./protocol";

interface PageReply { ok: true; enabled: boolean; pageKey: string }
type PageResponse = PageReply | { ok: false; reason?: string };
type PreferenceResponse = { ok: true; commonWordCutoff: number; hiddenCount: number } | { ok: false; reason?: string };
type RuntimeReply = { ok: true; status: RuntimeStatus } | { ok: false; reason: string };

// 消息通道异常时也必须结束等待；迟到结果不再改变弹窗状态。
function bounded<T>(operation: Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("message timeout")), 5000);
    operation.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}
let pageKey: string | null = null;
let tabId: number | undefined;
function el<T extends HTMLElement>(id: string): T { return document.getElementById(id) as T; }
function pageMessage(text: string, kind: "error" | "success" | "pending" = "error"): void {
  const status = el<HTMLDivElement>("page-status"); status.textContent = text; status.dataset.kind = kind; status.hidden = false;
}
const backendTarget = new URL(API_URL).host;
let runtimeStatus: RuntimeStatus | null = null;
function shortSoftwareVersion(version: string): string {
  const match = /^(\d+\.\d+\.\d+)(-SNAPSHOT\..+)?$/.exec(version);
  return match ? `${match[1]}${match[2] ? " · 开发版" : ""}` : "版本未知";
}
// 只复制排障需要的固定字段，不序列化消息原文、页面状态或用户数据。
function diagnosticText(): string {
  const value = runtimeStatus, published = value?.publishedDataset;
  return JSON.stringify({
    backend: new URL(API_URL).origin,
    extensionVersion: chrome.runtime.getManifest().version_name ?? chrome.runtime.getManifest().version,
    status: el<HTMLElement>("service-status").textContent,
    runtime: value ? {
      softwareVersion: value.softwareVersion, apiContract: value.apiContract,
      mode: value.mode, ready: value.ready, reason: value.reason, datasetVersion: value.datasetVersion,
      publishedDataset: published ? {
        releaseId: published.releaseId, preparationId: published.preparationId,
        ruleId: published.ruleId, sqlVersion: published.sqlVersion, sha256: published.sha256,
      } : null,
    } : null,
  }, null, 2);
}
async function copyDiagnostics(): Promise<void> {
  const button = el<HTMLButtonElement>("copy-diagnostics"), status = el<HTMLElement>("copy-status");
  button.disabled = true;
  status.textContent = "正在复制…"; status.dataset.kind = "pending"; status.hidden = false;
  try {
    await bounded(navigator.clipboard.writeText(diagnosticText()));
    status.textContent = "诊断信息已复制"; status.dataset.kind = "success";
  } catch {
    status.textContent = "复制失败，请重试。"; status.dataset.kind = "error";
  } finally { button.disabled = false; }
}
function backendState(connected = false, ready = false): void {
  const node = el<HTMLElement>("backend-target");
  node.textContent = `${connected ? "● 已连接" : "○ 未连接"} ${backendTarget}${connected && !ready ? " · 未就绪" : ""}`;
  node.dataset.kind = connected ? (ready ? "success" : "pending") : "error";
}
function showDiagnostics(open: boolean, restoreFocus = false): void {
  el<HTMLElement>("service-diagnostics").hidden = !open;
  const button = el<HTMLButtonElement>("diagnostics-toggle");
  button.setAttribute("aria-expanded", String(open));
  if (restoreFocus) button.focus();
}
function renderPageState(enabled: boolean): void {
  const status = el<HTMLDivElement>("page-status");
  status.textContent = enabled ? "已开启 · 英文优先" : "已关闭 · 保留英文";
  status.dataset.kind = "success";
  status.hidden = true;
}
async function readServiceStatus(): Promise<void> {
  const status = el<HTMLDivElement>("service-status"), identity = el<HTMLDivElement>("service-identity");
  el<HTMLElement>("backend-target").textContent = `○ 正在连接 ${backendTarget}`;
  status.textContent = "正在查询服务状态…";
  try {
    const reply = await bounded(chrome.runtime.sendMessage({ type: "runtime-status" })) as RuntimeReply | undefined;
    if (!reply || reply.ok !== true) {
      backendState();
      if (reply?.reason === "protocol-mismatch") {
        status.textContent = "协议不匹配，请使用匹配的扩展与服务版本。"; status.dataset.kind = "error"; identity.hidden = true; return;
      }
      if (reply?.reason === "software-version") {
        status.textContent = "软件版本不匹配，请安装与服务对应的扩展。"; status.dataset.kind = "error"; identity.hidden = true; return;
      }
      if (reply?.reason === "binding-missing") {
        status.textContent = "扩展缺少发行资料绑定，请使用正式发行包。"; status.dataset.kind = "error"; identity.hidden = true; return;
      }
      if (reply?.reason === "dataset-identity") {
        status.textContent = "已发布资料身份不匹配，请安装与服务对应的发行包。"; status.dataset.kind = "error"; identity.hidden = true; return;
      }
      if (reply?.reason === "invalid-response") {
        status.textContent = "运行状态响应无效，服务/API合同不兼容。"; status.dataset.kind = "error"; identity.hidden = true; return;
      }
      throw new Error("status unavailable");
    }
    const value = reply.status;
    runtimeStatus = value;
    const published = value.publishedDataset;
    identity.textContent = `软件 ${shortSoftwareVersion(value.softwareVersion)}`;
    identity.hidden = !(value.mode === "formal" && value.ready && published !== null);
    backendState(true, value.ready);
    if (value.reason === "INITIALIZING") { status.textContent = "服务正在初始化发布资料…"; status.dataset.kind = "pending"; }
    else if (value.mode === "demo") { status.textContent = "演示模式 · 非正式就绪"; status.dataset.kind = "error"; }
    else if (value.mode === "invalid" || value.reason === "SCHEMA_MISMATCH") { status.textContent = "资料结构不匹配，请使用匹配的服务与资料。"; status.dataset.kind = "error"; }
    else if (value.reason === "IDENTITY_MISMATCH") { status.textContent = "服务发行资料身份校验失败，请检查匹配的发行包。"; status.dataset.kind = "error"; }
    else if (value.reason === "NO_PUBLISHED_DATA") { status.textContent = "尚无已发布资料，请检查资料初始化与发布状态。"; status.dataset.kind = "error"; }
    else if (value.reason === "DEPENDENCY_UNAVAILABLE") { status.textContent = "服务依赖不可用，请检查后端依赖服务。"; status.dataset.kind = "error"; }
    else if (value.ready) { status.textContent = value.reason === "PREWARM_DEGRADED" ? "正式就绪 · 预热降级，服务可用。" : "正式就绪。"; status.dataset.kind = "success"; }
    else { status.textContent = "资料初始化未完成。"; status.dataset.kind = "error"; }
    status.hidden = value.mode === "formal" && value.ready && value.reason !== "PREWARM_DEGRADED";
  } catch {
    backendState();
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
    if (response?.ok === false && response.reason === "not-video-page") { pageMessage("打开 YouTube 视频即可使用字幕增强。", "pending"); return; }
    if (response === undefined || response === null) { pageMessage("打开 YouTube 视频即可使用字幕增强。", "pending"); return; }
    if (!validPageReply(response)) { pageMessage("当前页面不可用，请刷新 YouTube 视频页面后重试。"); return; }
    tabId = tab.id; pageKey = response.pageKey; toggle.checked = response.enabled; toggle.disabled = false;
    renderPageState(response.enabled);
  } catch { pageMessage("打开 YouTube 视频即可使用字幕增强。", "pending"); }
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
async function readPreferences(): Promise<void> {
  try {
    const response = await bounded(chrome.runtime.sendMessage({ type: "local-preferences", action: "read" })) as PreferenceResponse | undefined;
    if (!response || response.ok !== true || ![1000, 1500, 2000].includes(response.commonWordCutoff) || !Number.isInteger(response.hiddenCount) || response.hiddenCount < 0) throw new Error("invalid receipt");
    el<HTMLElement>("cutoff-summary").textContent = `Top ${response.commonWordCutoff}`;
    el<HTMLElement>("hidden-summary").textContent = `${response.hiddenCount} 条`;
  } catch {
    el<HTMLElement>("cutoff-summary").textContent = "不可用";
    el<HTMLElement>("hidden-summary").textContent = "不可用";
    const status = el<HTMLElement>("preference-status");
    status.textContent = "偏好读取失败，请重新打开弹窗重试。";
    status.hidden = false;
  }
}
document.addEventListener("DOMContentLoaded", () => {
  const diagnostics = el<HTMLButtonElement>("diagnostics-toggle");
  diagnostics.addEventListener("click", () => { showDiagnostics(el<HTMLElement>("service-diagnostics").hidden); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el<HTMLElement>("service-diagnostics").hidden) {
      event.preventDefault(); showDiagnostics(false, true);
    }
  });
  showDiagnostics(false);
  el<HTMLElement>("diagnostic-target").textContent = backendTarget;
  el<HTMLButtonElement>("copy-diagnostics").addEventListener("click", () => { void copyDiagnostics(); });
  void readServiceStatus();
  el<HTMLInputElement>("enhance-toggle").addEventListener("change", () => { void setPage(); });
  el<HTMLButtonElement>("open-manage").addEventListener("click", () => { void chrome.tabs.create({ url: chrome.runtime.getURL("manage.html") }); });
  void readPreferences();
  void readPage();
});
