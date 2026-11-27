import { isYouTubeVideoId, normalizeVideoTitle } from "./manage-context";

const requestEvent = "lexiflow-video-title-request";
const replyEvent = (videoId: string) => `lexiflow-video-title:${videoId}`;

/** 在 MAIN world 中只读取同一播放器返回的一对身份和标题，不请求网络。 */
export function installYouTubeVideoTitleSource(): void {
  document.addEventListener(requestEvent, event => {
    if (!(event instanceof CustomEvent) || !isYouTubeVideoId(event.detail)) return;
    const videoId = event.detail;
    try {
      const player = document.querySelector<HTMLElement & {
        getVideoData?: () => { video_id?: unknown; title?: unknown } | undefined;
      }>("#movie_player");
      const data = player?.getVideoData?.();
      if (data?.video_id !== videoId) return;
      const title = normalizeVideoTitle(data.title);
      if (title) document.dispatchEvent(new CustomEvent(replyEvent(videoId), { detail: title }));
    } catch { /* 播放器尚未就绪时，由管理页保留可用的来源链接。 */ }
  });
}

/** 临时监听同一次同步查询，不缓存真实视频标题，不使用 document.title。 */
export function readYouTubeVideoTitle(videoId: string): string | undefined {
  let title: string | undefined;
  const name = replyEvent(videoId);
  const receive = (event: Event) => {
    if (event instanceof CustomEvent) title = normalizeVideoTitle(event.detail);
  };
  document.addEventListener(name, receive);
  try {
    document.dispatchEvent(new CustomEvent(requestEvent, { detail: videoId }));
  } finally { document.removeEventListener(name, receive); }
  return title;
}
