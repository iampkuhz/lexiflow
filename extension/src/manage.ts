import type { HiddenPreference as Hidden, RecentItem } from "./preferences";
type Lexicon = Hidden & { frequencyRank: number | null; hintState: "eligible" | "blocked" };
type PreferencesReply = { ok: true; commonWordCutoff: number; hidden: Hidden[] } | { ok: false; reason?: string };
type RecentReply = { ok: true; items: RecentItem[] } | { ok: false; reason?: string };
type LexiconReply = { ok: true; items: Lexicon[]; total: number; page: number; pageSize: number } | { ok: false; reason?: string };
const PAGE_SIZE = 20;
const state = { tab: "recent", recent: [] as RecentItem[], hidden: [] as Hidden[], selected: new Set<string>(), localPage: 1, query: "", sort: "frequency", lexPage: 1, lexTotal: 0, request: 0, busy: false };
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
function cell(row: HTMLElement, text: string, className = ""): HTMLElement { const n = make("span", className); n.setAttribute("role", "cell"); n.textContent = text; row.append(n); return n; }
function heading(row: HTMLElement, labels: string[]): void { row.className += " head"; row.setAttribute("role", "row"); for (const label of labels) { const n = make("span"); n.setAttribute("role", "columnheader"); n.textContent = label; row.append(n); } }
function showTabs(): void {
  for (const name of ["recent", "hidden", "lexicon"]) { byId<HTMLButtonElement>(`${name}-tab`).setAttribute("aria-selected", String(state.tab === name)); byId<HTMLElement>(`${name}-panel`).hidden = state.tab !== name; }
  scheduleRecentRefresh();
}
function renderRecent(): void {
  const items = state.recent, total = items.length;
  const host = byId<HTMLDivElement>("recent-list"); host.replaceChildren(); host.hidden = total === 0;
  byId("recent-count").textContent = recentLoaded ? String(total) : "";
  byId("recent-empty").hidden = !recentLoaded || total > 0;
  if (!total) { byId("recent-pager").hidden = true; updateBulk(); return; }
  state.localPage = Math.min(state.localPage, Math.ceil(total / PAGE_SIZE));
  const hiddenIds = new Set(state.hidden.map(item => item.entryId));
  const head = make("div", "row recent-row"); heading(head, ["选择", "词条", "中文提示", "次数", "操作"]); host.append(head);
  const slice = items.slice((state.localPage - 1) * PAGE_SIZE, state.localPage * PAGE_SIZE);
  for (const item of slice) {
    const row = make("div", "row recent-row"); row.setAttribute("role", "row");
    const check = make("input"); check.type = "checkbox"; check.checked = state.selected.has(item.entryId); check.disabled = hiddenIds.has(item.entryId); check.dataset.pick = item.entryId; check.setAttribute("aria-label", `选择 ${item.lemma}`); cell(row, "").append(check);
    cell(row, item.lemma); cell(row, item.gloss || "—", "row-gloss recent-gloss"); cell(row, String(item.count), "number");
    const action = make("span"); action.setAttribute("role", "cell"); const hide = button(hiddenIds.has(item.entryId) ? "已隐藏" : "不再提示", "hide", item.entryId); hide.disabled = hiddenIds.has(item.entryId); action.append(hide); row.append(action); host.append(row);
  }
  pager("recent-pager", total, state.localPage, PAGE_SIZE, "recent"); updateBulk();
}
function renderHidden(): void {
  const items = state.hidden, total = items.length, pages = Math.max(1, Math.ceil(total / PAGE_SIZE)); state.localPage = Math.min(state.localPage, pages);
  byId("hidden-count").textContent = String(total);
  const host = byId<HTMLDivElement>("hidden-list"); host.replaceChildren();
  if (!total) { byId<HTMLParagraphElement>("hidden-empty").textContent = "没有已隐藏的词条。"; byId("hidden-empty").hidden = false; byId("hidden-pager").hidden = true; return; }
  byId("hidden-empty").hidden = true; const head = make("div", "row hidden-row"); heading(head, ["已隐藏词条", "中文提示", "操作"]); host.append(head);
  for (const item of items.slice((state.localPage - 1) * PAGE_SIZE, state.localPage * PAGE_SIZE)) { const row = make("div", "row hidden-row"); row.setAttribute("role", "row"); cell(row, item.lemma); cell(row, item.gloss || "—", "gloss"); const action = make("span"); action.setAttribute("role", "cell"); action.append(button("恢复提示", "restore", item.entryId)); row.append(action); host.append(row); }
  pager("hidden-pager", total, state.localPage, PAGE_SIZE, "hidden");
}
function renderLexicon(items: Lexicon[], total: number, page: number, pageSize: number): void {
  const host = byId<HTMLDivElement>("lexicon-list"); host.replaceChildren(); state.lexTotal = total;
  byId<HTMLParagraphElement>("lexicon-empty").hidden = total !== 0;
  byId<HTMLParagraphElement>("lexicon-empty").textContent = state.query ? `当前词库没有查到「${state.query}」。可换用原形或检查拼写。` : "当前没有词条。";
  if (!total) { byId("lexicon-pager").hidden = true; return; }
  const head = make("div", "row lex-row"); heading(head, ["英文词条", "词频排名", "中文提示", "系统提示"]); host.append(head);
  const fmt = new Intl.NumberFormat("zh-CN");
  for (const item of items) { const row = make("div", `row lex-row ${item.hintState === "eligible" ? "eligible-row" : "blocked-row"}`); row.setAttribute("role", "row"); cell(row, item.lemma); cell(row, item.frequencyRank === null ? "暂无" : fmt.format(item.frequencyRank), "number rank"); cell(row, item.gloss || "—", "gloss lex-gloss"); const status = cell(row, item.hintState === "eligible" ? "可提示" : "系统不提示", "row-state lex-state"); status.classList.add(item.hintState); host.append(row); }
  pager("lexicon-pager", total, page, pageSize, "lexicon");
}
function updateBulk(): void { const b = byId<HTMLButtonElement>("hide-selected"); b.disabled = state.selected.size === 0 || state.busy; b.textContent = state.selected.size ? `隐藏所选（${state.selected.size}）` : "隐藏所选"; }
async function readPreferences(): Promise<void> {
  const select = byId<HTMLSelectElement>("common-word-cutoff"), status = byId<HTMLDivElement>("cutoff-status"); select.disabled = true;
  try {
    const result = await send<PreferencesReply>({ type: "local-preferences", action: "read" });
    if (!result || result.ok !== true || ![1000, 1500, 2000].includes(result.commonWordCutoff) || !Array.isArray(result.hidden)) throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "本机偏好响应无效"));
    state.hidden = result.hidden; select.value = String(result.commonWordCutoff); select.dataset.previous = select.value; select.disabled = false;
    status.textContent = "当前设置仅保存在本机。"; status.dataset.kind = "success"; renderHidden(); renderRecent();
  } catch (error) { select.value = ""; select.disabled = true; status.textContent = `无法读取本机设置：${error instanceof Error ? error.message : "服务不可用"}`; status.dataset.kind = "error"; throw error; }
}
async function saveCutoff(): Promise<void> {
  const select = byId<HTMLSelectElement>("common-word-cutoff"), status = byId<HTMLDivElement>("cutoff-status");
  const value = Number(select.value), previous = select.dataset.previous ?? "1500"; select.disabled = true;
  status.textContent = "正在保存本机设置…"; status.dataset.kind = "pending";
  try { const result = await send<PreferencesReply>({ type: "local-preferences", action: "setCutoff", commonWordCutoff: value });
    if (!result || result.ok !== true || result.commonWordCutoff !== value || !Array.isArray(result.hidden)) throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "本机服务拒绝保存"));
    select.dataset.previous = String(value); status.textContent = `已保存：Top ${value}。新字幕提示将使用此设置。`; status.dataset.kind = "success";
  } catch (error) { select.value = previous; status.textContent = `保存失败，已恢复上次设置：${error instanceof Error ? error.message : "本机服务不可用"}`; status.dataset.kind = "error"; }
  finally { select.disabled = false; }
}
let recentRead = 0, recentTimer: number | undefined, recentLoaded = false;
function recentVisible(): boolean { return document.visibilityState === "visible" && state.tab === "recent"; }
function canRefreshRecent(): boolean { return recentVisible() && !state.busy && state.selected.size === 0; }
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
async function readLexicon(): Promise<void> {
  const id = ++state.request; feedback();
  try { const result = await send<LexiconReply>({ type: "local-lexicon", query: state.query, sort: state.sort, page: state.lexPage, pageSize: PAGE_SIZE }); if (id !== state.request) return;
    if (!result || result.ok !== true || !Array.isArray(result.items) || !Number.isInteger(result.total) || !Number.isInteger(result.page) || !Number.isInteger(result.pageSize)) throw new Error(describeReason(result && result.ok === false ? result.reason : undefined, "词库响应无效"));
    renderLexicon(result.items, result.total, result.page, result.pageSize);
  } catch (error) { if (id !== state.request) return; byId("lexicon-list").replaceChildren(); byId("lexicon-empty").hidden = true; byId("lexicon-pager").hidden = true; feedback(`词库查询失败：${error instanceof Error ? error.message : "本机服务不可用"}`, true); }
}
async function refreshManage(): Promise<void> { try { await Promise.all([readRecent(), readPreferences()]); feedback(); } catch (error) { feedback(`加载提示管理失败：${error instanceof Error ? error.message : "本机服务不可用"}`, true); } }
async function mutate(action: "hide" | "restore", item: Hidden): Promise<boolean> {
  const request = action === "hide" ? { type: "local-preferences", action, entryId: item.entryId, lemma: item.lemma, gloss: item.gloss } : { type: "local-preferences", action, entryId: item.entryId };
  const reply = await send<PreferencesReply>(request); if (!reply || reply.ok !== true || !Array.isArray(reply.hidden)) throw new Error(describeReason(reply && reply.ok === false ? reply.reason : undefined, "本机偏好服务拒绝保存")); state.hidden = reply.hidden; return true;
}
async function hideMany(ids: string[]): Promise<void> {
  if (state.busy) return; state.busy = true; updateBulk(); let successes = 0; const failures: string[] = [];
  for (const id of ids) { const item = state.recent.find(x => x.entryId === id); if (!item) { failures.push(id); continue; } try { await mutate("hide", item); successes++; } catch (error) { failures.push(`${item.lemma}：${error instanceof Error ? error.message : "保存失败"}`); } }
  state.busy = false; state.selected.clear(); await Promise.all([readRecent(), readPreferences().catch(()=>undefined)]); state.tab = "recent"; showTabs();
  feedback(failures.length ? `已保存 ${successes} 条；${failures.length} 条未保存：${failures.join("；")}` : `已将 ${successes} 条保存为本机隐藏偏好。`, failures.length > 0); updateBulk();
}
async function restoreOne(id: string): Promise<void> { const item = state.hidden.find(x => x.entryId === id); if (!item || state.busy) return; state.busy = true; try { await mutate("restore", item); await readPreferences(); feedback(`已恢复「${item.lemma}」；是否出现仍取决于当前规则。`); } catch (error) { feedback(`恢复「${item.lemma}」失败：${error instanceof Error ? error.message : "保存失败"}`, true); } finally { state.busy = false; renderHidden(); } }
document.addEventListener("DOMContentLoaded", () => {
  for (const name of ["recent", "hidden", "lexicon"]) byId<HTMLButtonElement>(`${name}-tab`).addEventListener("click", () => { state.tab = name; showTabs(); feedback(); if (name === "lexicon") void readLexicon(); else if (name === "recent") refreshVisibleRecent(); });
  document.addEventListener("visibilitychange", () => { refreshVisibleRecent(); scheduleRecentRefresh(); });
  byId<HTMLSelectElement>("common-word-cutoff").addEventListener("change", () => { void saveCutoff(); });
  byId<HTMLFormElement>("lexicon-search").addEventListener("submit", event => { event.preventDefault(); state.query = byId<HTMLInputElement>("query").value.trim().toLocaleLowerCase().replace(/’/g, "'").replace(/\s+/g, " "); state.lexPage = 1; if (state.tab !== "lexicon") { state.tab = "lexicon"; showTabs(); } void readLexicon(); });
  byId<HTMLSelectElement>("sort").addEventListener("change", event => { state.sort = (event.currentTarget as HTMLSelectElement).value; state.lexPage = 1; void readLexicon(); });
  byId<HTMLDivElement>("recent-list").addEventListener("change", event => { const input = event.target as HTMLInputElement; const id = input.dataset.pick; if (!id) return; if (input.checked) state.selected.add(id); else state.selected.delete(id); updateBulk(); if (!input.checked && state.selected.size === 0) refreshVisibleRecent(); });
  byId<HTMLDivElement>("recent-list").addEventListener("click", event => { const b = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-action]"); if (b?.dataset.action === "hide" && b.dataset.entry) void hideMany([b.dataset.entry]); });
  byId<HTMLDivElement>("hidden-list").addEventListener("click", event => { const b = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-action]"); if (b?.dataset.action === "restore" && b.dataset.entry) void restoreOne(b.dataset.entry); });
  byId<HTMLButtonElement>("hide-selected").addEventListener("click", () => { if (state.selected.size) void hideMany([...state.selected]); });
  for (const id of ["recent-pager", "hidden-pager", "lexicon-pager"]) byId<HTMLElement>(id).addEventListener("click", event => { const b = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-step]"); if (!b || b.disabled) return; const step = Number(b.dataset.step); const name = id.replace("-pager", ""); if (name === "lexicon") { state.lexPage = Math.max(1, state.lexPage + step); void readLexicon(); } else { state.localPage = Math.max(1, state.localPage + step); name === "recent" ? renderRecent() : renderHidden(); } });
  showTabs(); void refreshManage();
});
