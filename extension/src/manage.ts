import { videoContext } from "./manage-context";
import { attachManagementConnection } from "./manage-connection";
import { attachCurrentVideo } from "./manage-current-video";
import { createHintCard } from "./manage-hint-card";
import type { HiddenPreference as Hidden, RecentItem } from "./preferences";
type Lexicon = Omit<Hidden, "gloss"> & { gloss: string | null; hintState: "eligible" | "blocked" | "hidden" };
type PreferencesReply = { ok: true; commonWordCutoff: number; hidden: Hidden[] } | { ok: false; reason?: string };
type RecentReply = { ok: true; items: RecentItem[] } | { ok: false; reason?: string };
type LexiconReply = { ok: true; items: Lexicon[]; total: number; page: number; pageSize: number } | { ok: false; reason?: string };
const PAGE_SIZE = 20;
const RECENT_PAGE_SIZES = [20, 40, 80] as const;
type RecentPageSize = typeof RECENT_PAGE_SIZES[number];
const RECENT_PAGE_SIZE_KEY = "recentHintsPageSize";
const DEFAULT_RECENT_PAGE_SIZE = 40;
function readRecentPageSize(value: unknown): RecentPageSize {
  return RECENT_PAGE_SIZES.find(size => size === value) ?? DEFAULT_RECENT_PAGE_SIZE;
}
const matchText = (value: string): string => value.normalize("NFKC").trim().toLowerCase()
  .replace(/’/gu, "'").replace(/\s+/gu, " ");
const state = { tab: "recent", recent: [] as RecentItem[], hidden: [] as Hidden[], pending: null as string | null, recentPage: 1, recentSize: DEFAULT_RECENT_PAGE_SIZE as RecentPageSize, recentQuery: "", hiddenPage: 1, query: "", sort: "frequency", lexPage: 1, request: 0, busy: false };
const byId = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;
const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = ""): HTMLElementTagNameMap[K] => { const node = document.createElement(tag); node.className = className; return node; };
function send<T>(message: object): Promise<T> { return chrome.runtime.sendMessage(message) as Promise<T>; }
function describeReason(reason: string | undefined, fallback: string): string {
  if (!reason) return fallback;
  const known: Record<string, string> = { network: "无法连接本机服务", backend_unavailable: "本机服务暂不可用", rejected: "本机服务拒绝保存", "invalid-response": "本机服务响应无效", "invalid-request": "请求无效或词条身份不匹配" };
  return known[reason] ?? reason;
}
function feedback(message = "", error = false): void { const node = byId<HTMLParagraphElement>("feedback"); node.textContent = message; node.hidden = !message; node.style.color = error ? "var(--error)" : "var(--fg)"; node.style.background = error ? "var(--bad)" : "var(--ok)"; }
function button(text: string, action: string, id: string): HTMLButtonElement { const b = make("button"); b.type = "button"; b.textContent = text; b.dataset.action = action; b.dataset.entry = id; return b; }
function pager(id: string, total: number, page: number, size: number, action: string): void {
  const nav = byId<HTMLElement>(id), pages = Math.max(1, Math.ceil(total / size));
  nav.hidden = total === 0; nav.querySelector("[data-total]")!.textContent = `共 ${total} 条`;
  nav.querySelector("[data-page]")!.textContent = `第 ${page} / ${pages} 页`;
  (nav.querySelector('[data-step="-1"]') as HTMLButtonElement).disabled = page <= 1;
  (nav.querySelector('[data-step="1"]') as HTMLButtonElement).disabled = page >= pages;
  nav.dataset.action = action;
}
const tabs = ["recent", "current-video", "hidden", "lexicon", "connection"];
let showCurrentVideo: ((active: boolean) => void) | undefined;
function showTabs(): void {
  for (const name of tabs) { byId<HTMLButtonElement>(`${name}-tab`).setAttribute("aria-selected", String(state.tab === name)); byId<HTMLElement>(`${name}-panel`).hidden = state.tab !== name; }
  showCurrentVideo?.(state.tab === "current-video");
  scheduleRecentRefresh();
}
function announceRecent(message: string): void { byId("recent-announcement").textContent = message; }
function hasKeyboardFocus(): boolean { return !document.documentElement.hasAttribute("data-pointer-input"); }
function syncRecentCard(card: HTMLElement): void {
  const pending = state.pending === card.dataset.entry;
  const saving = pending && state.busy;
  const action = card.querySelector<HTMLButtonElement>(".recent-action")!;
  const undo = card.querySelector<HTMLButtonElement>(".recent-undo")!;
  card.toggleAttribute("data-pending", pending);
  action.textContent = pending ? "确认" : "不再提示";
  action.dataset.action = pending ? "confirm" : "stage";
  action.setAttribute("aria-label", `${action.textContent}「${card.querySelector(".hint-word")!.textContent}」`);
  if (saving) action.setAttribute("aria-disabled", "true"); else action.removeAttribute("aria-disabled");
  if (!pending) showRecentError(card, "");
  undo.hidden = !pending;
  undo.disabled = saving;
  card.querySelector<HTMLElement>(".hint-count")!.hidden = pending;
}
function setPending(id: string | null): void {
  const previous = state.pending;
  state.pending = id;
  byId("recent-list").querySelectorAll<HTMLElement>(".recent-card").forEach(card => {
    if (card.dataset.entry === previous || card.dataset.entry === id) syncRecentCard(card);
  });
}
function renderRecent(): void {
  const hiddenIds = new Set(state.hidden.map(item => item.entryId));
  const visible = state.recent.filter(item => !hiddenIds.has(item.entryId));
  const query = state.recentQuery;
  const items = query ? visible.filter(item => matchText(item.lemma).includes(query) || matchText(item.gloss).includes(query)) : visible;
  const total = items.length, pageSize = state.recentSize;
  const host = byId<HTMLUListElement>("recent-list");
  const active = document.activeElement;
  const focused = hasKeyboardFocus() && active instanceof HTMLElement ? active.closest<HTMLElement>(".recent-card") : null;
  const previousIndex = focused ? Array.from(host.children).indexOf(focused) : -1;
  if (state.pending !== null && !items.some(item => item.entryId === state.pending)) state.pending = null;
  host.hidden = total === 0;
  byId("recent-count").textContent = recentLoaded ? String(visible.length) : "";
  const empty = byId("recent-empty");
  empty.hidden = !recentLoaded || total > 0;
  empty.textContent = visible.length && query ? "没有匹配的提示。试试更短的关键词。" : "近 7 天没有可管理的提示。";
  const searchStatus = byId("recent-search-status");
  const resultText = recentLoaded && query && total ? `搜索结果 ${total} 条` : "";
  if (searchStatus.textContent !== resultText) searchStatus.textContent = resultText;
  state.recentPage = Math.min(state.recentPage, Math.max(1, Math.ceil(total / pageSize)));
  const cards = items.slice((state.recentPage - 1) * pageSize, state.recentPage * pageSize).map(item => {
    const { card, rail } = createHintCard(item.lemma, item.gloss, item.frequencyRank, item.count);
    card.classList.add("recent-card"); card.dataset.entry = item.entryId;
    const action = button("不再提示", "stage", item.entryId); action.className = "recent-action";
    const undo = button("撤销", "undo", item.entryId); undo.className = "recent-undo";
    undo.setAttribute("aria-label", `撤销「${item.lemma}」`);
    const actions = make("div", "recent-actions"); actions.append(undo, action);
    rail.append(actions);
    const error = make("p", "recent-save-error"); error.id = `recent-error-${item.entryId}`; error.hidden = true;
    action.setAttribute("aria-describedby", error.id);
    card.append(error); syncRecentCard(card);
    return card;
  });
  host.replaceChildren(...cards);
  if (focused && state.tab === "recent") {
    const target = cards.find(card => card.dataset.entry === focused.dataset.entry) ??
      cards[Math.min(previousIndex, cards.length - 1)];
    (target?.querySelector<HTMLElement>(".recent-action") ?? byId("recent-tab")).focus({ preventScroll: true });
  }
  pager("recent-pager", total, state.recentPage, pageSize, "recent");
}
function renderHidden(): void {
  const items = state.hidden, total = items.length, pages = Math.max(1, Math.ceil(total / PAGE_SIZE)); state.hiddenPage = Math.min(state.hiddenPage, pages);
  byId("hidden-count").textContent = String(total);
  const host = byId<HTMLUListElement>("hidden-list");
  host.hidden = total === 0;
  byId("hidden-empty").hidden = total > 0;
  host.replaceChildren(...items.slice((state.hiddenPage - 1) * PAGE_SIZE, state.hiddenPage * PAGE_SIZE).map(item => {
    const { card, rail } = createHintCard(item.lemma, item.gloss, item.frequencyRank);
    const restore = button("恢复提示", "restore", item.entryId);
    restore.className = "restore-action";
    restore.setAttribute("aria-label", `恢复提示「${item.lemma}」`);
    rail.append(restore);
    return card;
  }));
  pager("hidden-pager", total, state.hiddenPage, PAGE_SIZE, "hidden");
}
function renderLexicon(items: Lexicon[], total: number, page: number, pageSize: number): void {
  const host = byId<HTMLUListElement>("lexicon-list");
  host.hidden = items.length === 0;
  const empty = byId("lexicon-empty");
  empty.hidden = items.length !== 0;
  empty.textContent = total > 0 ? "这一页没有词条。" : state.query ? `没有查到包含「${state.query}」的词条。` : "当前没有词条。";
  host.replaceChildren(...items.map(item => {
    const { card, rail } = createHintCard(item.lemma, item.gloss, item.frequencyRank);
    const status = make("span", `row-state ${item.hintState}`);
    status.textContent = { eligible: "可提示", blocked: "系统不提示", hidden: "已停止提示" }[item.hintState];
    rail.append(status);
    return card;
  }));
  pager("lexicon-pager", total, page, pageSize, "lexicon");
}
async function readPreferences(): Promise<void> {
  const select = byId<HTMLSelectElement>("common-word-cutoff"), status = byId("cutoff-status"); select.disabled = true;
  try {
    const result = await send<PreferencesReply>({ type: "local-preferences", action: "read" });
    if (!result || result.ok !== true || ![1000, 1500, 2000].includes(result.commonWordCutoff) || !Array.isArray(result.hidden)) throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "本机偏好响应无效"));
    state.hidden = result.hidden; select.value = String(result.commonWordCutoff); select.dataset.previous = select.value; select.disabled = false;
    status.textContent = "仅保存在本机。"; delete status.dataset.kind; renderHidden(); renderRecent();
  } catch (error) { select.value = ""; select.disabled = true; status.textContent = `无法读取本机设置：${error instanceof Error ? error.message : "服务不可用"}`; status.dataset.kind = "error"; throw error; }
}
async function saveCutoff(): Promise<void> {
  const select = byId<HTMLSelectElement>("common-word-cutoff"), status = byId("cutoff-status");
  const value = Number(select.value), previous = select.dataset.previous ?? "1500"; select.disabled = true;
  status.textContent = "正在保存本机设置…"; status.dataset.kind = "pending";
  try { const result = await send<PreferencesReply>({ type: "local-preferences", action: "setCutoff", commonWordCutoff: value });
    if (!result || result.ok !== true || result.commonWordCutoff !== value || !Array.isArray(result.hidden)) throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "本机服务拒绝保存"));
    select.dataset.previous = String(value); status.textContent = `已保存：前 ${value} 词。`; status.dataset.kind = "success";
  } catch (error) { select.value = previous; status.textContent = `保存失败，已恢复上次设置：${error instanceof Error ? error.message : "本机服务不可用"}`; status.dataset.kind = "error"; }
  finally { select.disabled = false; }
}
let recentRead = 0, recentTimer: number | undefined, recentLoaded = false, recentInitialized = false;
function recentVisible(): boolean { return document.visibilityState === "visible" && state.tab === "recent"; }
function canRefreshRecent(): boolean { return recentInitialized && recentVisible() && !state.busy && state.pending === null; }
async function readRecent(automatic = false): Promise<void> {
  const id = ++recentRead;
  const current = () => id === recentRead && (!automatic || canRefreshRecent());
  const status = byId<HTMLParagraphElement>("recent-status");
  try {
    const result = await send<RecentReply>({ type: "local-recent" });
    if (!current()) return;
    if (!result || result.ok !== true || !Array.isArray(result.items))
      throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "最近提示响应无效"));
    const changed = !recentLoaded || JSON.stringify(state.recent) !== JSON.stringify(result.items);
    recentLoaded = true; state.recent = result.items; status.hidden = true;
    if (!automatic || changed) renderRecent();
  } catch (error) {
    if (!current()) return;
    recentLoaded = false; state.recent = []; renderRecent();
    status.textContent = `无法读取近 7 天提示：${error instanceof Error ? error.message : "本机服务不可用"}`;
    status.hidden = false;
  }
}
function refreshVisibleRecent(): void { if (canRefreshRecent()) void readRecent(true); }
function scheduleRecentRefresh(): void {
  window.clearTimeout(recentTimer);
  if (recentVisible()) recentTimer = window.setTimeout(() => {
    refreshVisibleRecent(); scheduleRecentRefresh();
  }, 60_000 - Date.now() % 60_000);
}
let lexiconReading = false, lexiconQueued = false;
function queueLexicon(reset = true): void {
  ++state.request; lexiconQueued = true;
  feedback();
  byId("lexicon-loading").hidden = false;
  byId("lexicon-empty").hidden = true;
  if (reset) {
    byId("lexicon-list").hidden = true;
    byId("lexicon-pager").hidden = true;
  }
  void readLexicon();
}
async function readLexicon(): Promise<void> {
  if (lexiconReading || !lexiconQueued || state.tab !== "lexicon") return;
  lexiconReading = true; lexiconQueued = false;
  const id = state.request, { query, sort, lexPage: page } = state;
  try {
    const result = await send<LexiconReply>({ type: "local-lexicon", query, sort, page, pageSize: PAGE_SIZE });
    if (id !== state.request) return;
    if (!result || result.ok !== true || !Array.isArray(result.items) || result.items.length > PAGE_SIZE ||
        !Number.isSafeInteger(result.total) || result.total < result.items.length || result.page !== page || result.pageSize !== PAGE_SIZE)
      throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "词库响应无效"));
    renderLexicon(result.items, result.total, result.page, result.pageSize);
  } catch (error) {
    if (id !== state.request) return;
    byId("lexicon-list").hidden = true;
    byId("lexicon-pager").hidden = true;
    feedback(`词库查询失败：${error instanceof Error ? error.message : "本机服务不可用"}`, true);
  } finally {
    lexiconReading = false;
    if (id === state.request) byId("lexicon-loading").hidden = true;
    if (lexiconQueued) void readLexicon();
  }
}
async function refreshManage(): Promise<void> {
  try { await readPreferences(); }
  catch (error) { feedback(`加载提示管理失败：${error instanceof Error ? error.message : "本机服务不可用"}`, true); }
  recentInitialized = true; await readRecent();
}
async function mutate(action: "hide" | "restore", item: Hidden): Promise<void> {
  const request = { type: "local-preferences", action, entryId: item.entryId };
  const reply = await send<PreferencesReply>(request);
  if (!reply || reply.ok !== true || !Array.isArray(reply.hidden))
    throw new Error(describeReason(reply && reply.ok === false ? reply.reason : undefined, "本机偏好服务拒绝保存"));
  if (reply.hidden.some(hidden => hidden.entryId === item.entryId) !== (action === "hide"))
    throw new Error("本机偏好未确认保存");
  state.hidden = reply.hidden;
}
function showRecentError(card: HTMLElement, message: string): void {
  const error = card.querySelector<HTMLParagraphElement>(".recent-save-error")!;
  error.textContent = message; error.hidden = !message;
}
function setBusy(value: boolean): void {
  state.busy = value;
  byId<HTMLInputElement>("recent-query").disabled = value;
  byId<HTMLSelectElement>("recent-page-size").disabled = value;
}
function resetRecentPage(): void {
  setPending(null);
  state.recentPage = 1;
  renderRecent();
}
function attachRecentFilters(): void {
  const query = byId<HTMLInputElement>("recent-query");
  const size = byId<HTMLSelectElement>("recent-page-size");
  let sizeChanged = false;
  const applyQuery = () => {
    const next = matchText(query.value);
    if (state.busy || state.recentQuery === next) return;
    state.recentQuery = next;
    resetRecentPage();
  };
  query.addEventListener("input", event => { if (!(event as InputEvent).isComposing) applyQuery(); });
  query.addEventListener("compositionend", applyQuery);
  size.addEventListener("change", () => {
    if (state.busy) return;
    sizeChanged = true;
    const next = readRecentPageSize(Number(size.value));
    if (state.recentSize === next) return;
    state.recentSize = next;
    resetRecentPage();
    void (async () => {
      try { await chrome.storage.local.set({ [RECENT_PAGE_SIZE_KEY]: next }); }
      catch { /* The choice still works in this tab if local storage is unavailable. */ }
    })();
  });
  void (async () => {
    try {
      const saved = await chrome.storage.local.get(RECENT_PAGE_SIZE_KEY);
      if (sizeChanged || state.busy) return;
      state.recentSize = readRecentPageSize(saved[RECENT_PAGE_SIZE_KEY]);
      size.value = String(state.recentSize);
      if (recentLoaded) renderRecent();
    } catch { /* Keep the default without blocking the list. */ }
  })();
}
async function confirmHide(item: RecentItem, card: HTMLElement): Promise<void> {
  setBusy(true); showRecentError(card, ""); syncRecentCard(card); feedback();
  announceRecent(`正在保存「${item.lemma}」的偏好。`);
  try {
    await mutate("hide", item);
    state.pending = null;
    renderRecent(); renderHidden();
    announceRecent(`已停止提示「${item.lemma}」，可在「已停止提示」恢复。`);
  } catch (error) {
    const message = `停止提示「${item.lemma}」失败：${error instanceof Error ? error.message : "保存失败"}`;
    if (state.tab === "recent") { showRecentError(card, message); announceRecent(message); }
    else { state.pending = null; feedback(message, true); }
  } finally { setBusy(false); if (card.isConnected) syncRecentCard(card); }
}
async function restoreOne(id: string): Promise<void> {
  const item = state.hidden.find(x => x.entryId === id);
  if (!item || state.busy) return;
  setBusy(true);
  try {
    await mutate("restore", item); renderRecent(); renderHidden();
    feedback(`已恢复「${item.lemma}」；是否出现仍取决于当前规则。`);
  } catch (error) { feedback(`恢复「${item.lemma}」失败：${error instanceof Error ? error.message : "保存失败"}`, true); }
  finally { setBusy(false); }
}
document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("pointerdown", () => document.documentElement.setAttribute("data-pointer-input", ""), true);
  document.addEventListener("keydown", () => document.documentElement.removeAttribute("data-pointer-input"), true);
  showCurrentVideo = attachCurrentVideo(videoContext(location.hash));
  attachManagementConnection(() => { void refreshManage(); });
  for (const name of tabs) byId<HTMLButtonElement>(`${name}-tab`).addEventListener("click", () => {
    if (name !== state.tab && !state.busy) setPending(null);
    if (state.tab === "lexicon" && name !== "lexicon") { ++state.request; lexiconQueued = false; }
    state.tab = name; showTabs(); feedback();
    if (name === "lexicon") queueLexicon(); else if (name === "recent") refreshVisibleRecent();
  });
  document.addEventListener("visibilitychange", () => { refreshVisibleRecent(); scheduleRecentRefresh(); });
  byId<HTMLSelectElement>("common-word-cutoff").addEventListener("change", () => { void saveCutoff(); });
  const lexiconQuery = byId<HTMLInputElement>("query");
  const applyLexiconQuery = () => {
    const query = lexiconQuery.value.normalize("NFC").trim().toLowerCase().replace(/’/g, "'").replace(/\s+/g, " ");
    if (query === state.query || state.tab !== "lexicon") return;
    state.query = query; state.lexPage = 1; queueLexicon();
  };
  lexiconQuery.addEventListener("input", event => { if (!(event as InputEvent).isComposing) applyLexiconQuery(); });
  lexiconQuery.addEventListener("compositionend", applyLexiconQuery);
  byId("lexicon-search").addEventListener("submit", event => { event.preventDefault(); applyLexiconQuery(); });
  byId<HTMLSelectElement>("sort").addEventListener("change", event => { state.sort = (event.currentTarget as HTMLSelectElement).value; state.lexPage = 1; queueLexicon(); });
  byId("recent-list").addEventListener("click", event => {
    const b = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-action]");
    const card = b?.closest<HTMLElement>(".recent-card"), id = b?.dataset.entry;
    if (!b || !card || !id || state.busy) return;
    const item = state.recent.find(entry => entry.entryId === id);
    if (!item) return;
    if (b.dataset.action === "stage") {
      setPending(id); feedback(); announceRecent(`是否停止提示「${item.lemma}」？可撤销或确认。`);
    } else if (state.pending === id && b.dataset.action === "undo") {
      if (hasKeyboardFocus()) card.querySelector<HTMLButtonElement>(".recent-action")!.focus();
      setPending(null); feedback(); announceRecent(`已撤销，保留「${item.lemma}」的提示。`);
    } else if (state.pending === id && b.dataset.action === "confirm") void confirmHide(item, card);
  });
  byId("hidden-list").addEventListener("click", event => { const b = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-action]"); if (b?.dataset.action === "restore" && b.dataset.entry) void restoreOne(b.dataset.entry); });
  for (const id of ["recent-pager", "hidden-pager", "lexicon-pager"]) byId<HTMLElement>(id).addEventListener("click", event => {
    const b = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-step]");
    if (!b || b.disabled) return;
    const step = Number(b.dataset.step);
    if (id === "lexicon-pager") { if (!lexiconReading) { state.lexPage = Math.max(1, state.lexPage + step); queueLexicon(false); } }
    else if (id === "hidden-pager") { state.hiddenPage = Math.max(1, state.hiddenPage + step); renderHidden(); }
    else if (!state.busy) { setPending(null); state.recentPage = Math.max(1, state.recentPage + step); renderRecent(); }
  });
  attachRecentFilters(); showTabs(); void refreshManage();
});
