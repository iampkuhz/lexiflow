interface PageReply { ok: true; enabled: boolean; pageKey: string }
type PageResponse = PageReply | { ok: false };
type PreferenceResponse = { ok: true; entryKeys: string[] } | { ok: false; reason?: string };

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
function validPageReply(value: unknown): value is PageReply {
  const result = value as PageReply | undefined;
  return !!result && result.ok === true && typeof result.enabled === "boolean" && typeof result.pageKey === "string" && result.pageKey.length > 0;
}
async function readPage(): Promise<void> {
  const toggle = el<HTMLInputElement>("enhance-toggle"); toggle.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (typeof tab?.id !== "number") { pageMessage("没有可用标签页，请打开 YouTube 视频页面。"); return; }
    const response = await chrome.tabs.sendMessage(tab.id, { type: "page-enhancement", action: "read" }) as PageResponse | undefined;
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
    const response = await chrome.tabs.sendMessage(tabId, { type: "page-enhancement", action: "set", enabled: desired, pageKey }) as PageResponse | undefined;
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
    const response = await chrome.runtime.sendMessage({ type: "local-preferences", action: "restore-all" }) as PreferenceResponse | undefined;
    if (!response || response.ok !== true || !Array.isArray(response.entryKeys) || response.entryKeys.length !== 0) throw new Error("invalid receipt");
    restoreMessage("已恢复全部提示偏好。", "success");
    el<HTMLDivElement>("restore-confirmation").hidden = true;
    start.hidden = false;
    start.focus();
  } catch { restoreMessage("恢复失败；本机偏好未确认更改，请重试。", "error"); }
  finally { restoreBusy = false; start.disabled = false; confirm.disabled = false; cancel.disabled = false; }
}
document.addEventListener("DOMContentLoaded", () => {
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
