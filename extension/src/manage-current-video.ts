import { bounded } from "./message-deadline";
import type { VideoContext } from "./manage-context";
import { isVocabularyRow, type VocabularyRow } from "./hint-vocabulary";

/** A management tab reads one acknowledged video session, never another active tab. */
export function attachCurrentVideo(context: VideoContext | undefined): (active: boolean) => void {
  const list = document.querySelector<HTMLUListElement>("#current-video-list")!;
  const status = document.querySelector<HTMLElement>("#current-video-status")!;
  const refresh = document.querySelector<HTMLButtonElement>("#refresh-current-video")!;
  let generation = 0, timer: number | undefined, active = false, expiry = false, rendered = JSON.stringify([]);
  const show = (message: string, rows: VocabularyRow[] = []): void => {
    status.textContent = message;
    const next = JSON.stringify(rows);
    if (rendered === next) return;
    rendered = next;
    const cards = rows.map(row => {
      const card = document.createElement("li");
      card.className = "current-word";
      const rank = document.createElement("span");
      rank.className = "hint-rank";
      if (row.frequencyRank !== null) {
        rank.textContent = `#${row.frequencyRank}`;
        rank.setAttribute("aria-label", `词频第 ${row.frequencyRank} 名`);
      }
      const term = document.createElement("span");
      term.className = "recent-term";
      const english = document.createElement("strong");
      english.className = "recent-word"; english.lang = "en"; english.textContent = row.english;
      const gloss = document.createElement("span");
      gloss.className = "recent-gloss"; gloss.textContent = row.chinese;
      term.append(english, gloss);
      const count = document.createElement("span");
      count.className = "current-count"; count.textContent = `提示 ${row.hintCount} 次`;
      card.append(rank, term, count);
      return card;
    });
    list.replaceChildren(...cards);
  };
  function expire(): void {
    expiry = true;
    generation++;
    window.clearTimeout(timer);
    refresh.disabled = true;
    show("原视频已切换或关闭。请从要查看的视频重新打开管理页。");
  }
  async function read(): Promise<void> {
    window.clearTimeout(timer);
    if (!active || expiry || !context || document.hidden) return;
    const id = ++generation;
    try {
      const reply = await bounded(chrome.tabs.sendMessage(context.tabId, { type: "page-vocabulary", pageKey: context.pageKey })) as
        { ok?: boolean; pageKey?: string; rows?: unknown } | undefined;
      if (id !== generation) return;
      if (!reply?.ok || reply.pageKey !== context.pageKey || !Array.isArray(reply.rows) || !reply.rows.every(isVocabularyRow)) {
        expire(); return;
      }
      show(reply.rows.length ? "仅统计这个视频页面的提示。" : "这个视频还没有词汇提示。", reply.rows);
    } catch { if (id === generation) show("无法读取原视频，请刷新或从视频页重新打开管理页。"); }
    finally {
      if (id === generation && active && !expiry && !document.hidden) timer = window.setTimeout(() => void read(), 2000);
    }
  }
  refresh.disabled = !context;
  if (!context) show("请在 YouTube 视频页打开 LexiFlow，再点「打开管理页」。");
  refresh.addEventListener("click", () => { void read(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { generation++; window.clearTimeout(timer); }
    else void read();
  });
  chrome.tabs?.onRemoved?.addListener(tab => { if (context && tab === context.tabId) expire(); });
  return shown => {
    if (active === shown) return;
    active = shown;
    if (shown) void read();
    else { generation++; window.clearTimeout(timer); }
  };
}
