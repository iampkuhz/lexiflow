import { mismatchText } from "./runtime-status";
import { bounded } from "./message-deadline";
import { CAPTION_LINE_LIMIT_KEY, DEFAULT_CAPTION_LINE_LIMIT, captionLineLimit } from "./caption-display";
import type { RuntimeStatus } from "./runtime-status";
import { DEFAULT_ORIGIN, loopbackOrigin, TEST_CHANNEL } from "./backend-target";
import { managementUrl } from "./manage-context";

interface PageReply { ok: true; enabled: boolean; pageKey: string; captionStatus?: string }
type PageResponse = PageReply | { ok: false; reason?: string };
type RuntimeReply = ({ ok: true; status: RuntimeStatus } | { ok: false; reason: string; expected?: string; actual?: string }) & { origin?: string };

let pageKey: string | null = null;
let tabId: number | undefined;
function el<T extends HTMLElement>(id: string): T { return document.getElementById(id) as T; }
function pageMessage(text: string, kind: "error" | "success" | "pending" = "error"): void {
  const status = el<HTMLDivElement>("page-status"); status.textContent = text; status.dataset.kind = kind;
}
let backendOrigin = DEFAULT_ORIGIN;
let statusGeneration = 0;
const backendHost = () => backendOrigin.slice("http://".length);
function backendState(connected = false, ready = false): void {
  const node = el<HTMLElement>("backend-target");
  node.textContent = `${connected ? "● 已连接" : "○ 未连接"} ${backendHost()}${connected && !ready ? " · 未就绪" : ""}`;
  node.dataset.kind = connected ? (ready ? "success" : "pending") : "error";
}
function renderPageState(enabled: boolean, captionStatus?: string): void {
  const status = el<HTMLDivElement>("page-status");
  const messages: Record<string, string> = {
    "no-hint": "已识别英文字幕 · 当前没有可显示的词汇提示",
    "no-caption": "等待播放器显示英文字幕",
    "waiting": "已识别字幕 · 正在查词",
    "shown": "正在显示中文词汇提示",
    "unavailable": "本句查词失败 · 保留英文",
    "preferences-unavailable": "本机偏好不可用 · 保留英文"
  };
  status.textContent = enabled ? messages[captionStatus ?? ""] ?? "" : "已关闭 · 保留英文";
  status.dataset.kind = "success";
}
function renderServiceStatus(reply?: RuntimeReply): void {
  const status = el<HTMLDivElement>("service-status");
  const origin = loopbackOrigin(reply?.origin);
  if (origin) backendOrigin = origin;
  if (!reply?.ok) {
    backendState();
    status.dataset.kind = "error";
    switch (reply?.reason) {
      case "protocol-mismatch": status.textContent = mismatchText(reply); break;
      case "invalid-response": status.textContent = "运行状态响应无效，服务/API合同不兼容。"; break;
      default: status.textContent = "无法连接服务，请检查后端是否启动。";
    }
    return;
  }
  const value = reply.status;
  backendState(true, value.ready);
  if (value.reason === "INITIALIZING") { status.textContent = "服务正在初始化发布资料…"; status.dataset.kind = "pending"; }
  else if (value.mode === "demo") { status.textContent = "演示模式 · 非正式就绪"; status.dataset.kind = "error"; }
  else if (value.mode === "invalid" || value.reason === "SCHEMA_MISMATCH") { status.textContent = "资料结构不匹配，请使用匹配的服务与资料。"; status.dataset.kind = "error"; }
  else if (value.reason === "IDENTITY_MISMATCH") { status.textContent = "服务发行资料身份校验失败，请检查匹配的发行包。"; status.dataset.kind = "error"; }
  else if (value.reason === "NO_PUBLISHED_DATA") { status.textContent = "尚无已发布资料，请检查资料初始化与发布状态。"; status.dataset.kind = "error"; }
  else if (value.reason === "DEPENDENCY_UNAVAILABLE") { status.textContent = "服务依赖不可用，请检查后端依赖服务。"; status.dataset.kind = "error"; }
  else if (value.ready) { status.textContent = value.reason === "PREWARM_DEGRADED" ? "正式就绪 · 预热降级，服务可用。" : "正式就绪。"; status.dataset.kind = "success"; }
  else { status.textContent = "资料初始化未完成。"; status.dataset.kind = "error"; }
  if (value.mode === "formal" && value.ready && value.reason !== "PREWARM_DEGRADED") status.textContent = "";
}
async function readServiceStatus(): Promise<void> {
  const generation = ++statusGeneration;
  const status = el<HTMLDivElement>("service-status");
  el<HTMLElement>("backend-target").textContent = `○ 正在连接 ${backendHost()}`;
  status.textContent = "";
  try {
    const reply = await bounded(chrome.runtime.sendMessage({ type: "runtime-status" })) as RuntimeReply | undefined;
    if (generation === statusGeneration) renderServiceStatus(reply);
  } catch {
    if (generation === statusGeneration) renderServiceStatus();
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
    if (response?.ok === false && response.reason === "other-channel") {
      pageMessage("另一套 LexiFlow 正在此页运行。请在 chrome://extensions 关闭另一套，再刷新视频。"); return;
    }
    if (!validPageReply(response)) { pageMessage("当前页面不可用，请刷新 YouTube 视频页面后重试。"); return; }
    tabId = tab.id; pageKey = response.pageKey; toggle.checked = response.enabled; toggle.disabled = false;
    renderPageState(response.enabled, response.captionStatus);
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
    toggle.checked = response.enabled; toggle.disabled = false; renderPageState(response.enabled, response.captionStatus);
  } catch { toggle.checked = !desired; pageMessage("设置未成功，请重新打开弹窗后重试。"); }
}
function attachCaptionLineLimit(): void {
  const select = el<HTMLSelectElement>("caption-line-limit"), status = el<HTMLElement>("caption-line-status");
  let saved = DEFAULT_CAPTION_LINE_LIMIT;
  select.disabled = true;
  void (async () => {
    try { saved = captionLineLimit((await bounded(chrome.storage.local.get(CAPTION_LINE_LIMIT_KEY)))[CAPTION_LINE_LIMIT_KEY]); }
    catch { status.textContent = "无法读取字幕行数设置。"; return; }
    select.value = String(saved); select.disabled = false;
  })();
  select.addEventListener("change", async () => {
    const requested = captionLineLimit(Number(select.value));
    select.disabled = true; status.textContent = "";
    try {
      await bounded(chrome.storage.local.set({ [CAPTION_LINE_LIMIT_KEY]: requested }));
      saved = requested;
    } catch { status.textContent = "字幕行数保存失败，已恢复原设置。"; }
    finally { select.value = String(saved); select.disabled = false; }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  if (TEST_CHANNEL) el<HTMLElement>("extension-name").textContent = "LexiFlow 测试版";
  attachCaptionLineLimit();
  el<HTMLInputElement>("enhance-toggle").addEventListener("change", () => { void setPage(); });
  el<HTMLButtonElement>("open-manage").addEventListener("click", () => {
    const context = tabId !== undefined && pageKey !== null ? { tabId, pageKey } : undefined;
    void chrome.tabs.create({ url: managementUrl(chrome.runtime.getURL("manage.html"), context) })
      .catch(() => pageMessage("无法打开管理页，请重试。"));
  });
  void readServiceStatus();
  void readPage();
});
