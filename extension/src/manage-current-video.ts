import { bounded } from "./message-deadline";
import { createHintCard } from "./manage-hint-card";
import { isYouTubeVideoId, type VideoContext } from "./manage-context";
import { isVocabularyRow, type VocabularyRow } from "./hint-vocabulary";

/** A management tab reads one acknowledged video session, never another active tab. */
export function attachCurrentVideo(context: VideoContext | undefined): (active: boolean) => void {
  const list = document.querySelector<HTMLUListElement>("#current-video-list")!;
  const status = document.querySelector<HTMLElement>("#current-video-status")!;
  const refresh = document.querySelector<HTMLButtonElement>("#refresh-current-video")!;
  const source = document.querySelector<HTMLElement>("#current-video-source")!;
  const link = document.querySelector<HTMLAnchorElement>("#current-video-link")!;
  let generation = 0, timer: number | undefined, active = false, expiry = false, rendered = JSON.stringify([]);
  const show = (message: string, rows: VocabularyRow[] = [], videoId?: string): void => {
    if (status.textContent !== message) status.textContent = message;
    const address = videoId ? `youtube.com/watch?v=${videoId}` : "";
    if (link.textContent !== address) {
      source.hidden = !address;
      if (address) link.href = `https://www.${address}`;
      else link.removeAttribute("href");
      link.textContent = address;
    }
    const next = JSON.stringify(rows);
    if (rendered === next) return;
    rendered = next;
    list.replaceChildren(...rows.map(row =>
      createHintCard(row.english, row.chinese, row.frequencyRank, row.hintCount).card));
  };
  function expire(): void {
    expiry = true;
    generation++;
    window.clearTimeout(timer);
    refresh.disabled = true;
    show("原视频已切换或关闭。请从要查看的视频重新打开管理页。");
  }
  function suspend(): void {
    generation++;
    window.clearTimeout(timer);
    if (context && !expiry) show("正在读取当前视频…");
  }
  async function read(): Promise<void> {
    window.clearTimeout(timer);
    if (!active || expiry || !context || document.hidden) return;
    const id = ++generation;
    try {
      const reply = await bounded(chrome.tabs.sendMessage(context.tabId, { type: "page-vocabulary", pageKey: context.pageKey })) as
        { ok?: boolean; pageKey?: string; videoId?: unknown; rows?: unknown } | undefined;
      if (id !== generation) return;
      if (!reply?.ok || reply.pageKey !== context.pageKey || !isYouTubeVideoId(reply.videoId) ||
          !Array.isArray(reply.rows) || !reply.rows.every(isVocabularyRow)) {
        expire(); return;
      }
      show(reply.rows.length ? "" : "这个视频还没有词汇提示。", reply.rows, reply.videoId);
    } catch { if (id === generation) show("无法读取原视频，请刷新或从视频页重新打开管理页。"); }
    finally {
      if (id === generation && active && !expiry && !document.hidden) timer = window.setTimeout(() => void read(), 2000);
    }
  }
  refresh.disabled = !context;
  if (!context) show("请在 YouTube 视频页打开 LexiFlow，再点「打开管理页」。");
  refresh.addEventListener("click", () => { void read(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) suspend();
    else void read();
  });
  chrome.tabs?.onRemoved?.addListener(tab => { if (context && tab === context.tabId) expire(); });
  return shown => {
    if (active === shown) return;
    active = shown;
    if (shown) void read();
    else suspend();
  };
}
