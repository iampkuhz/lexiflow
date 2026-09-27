/** MAIN-world 被动采集 JSON3 字幕；不主动请求、不上传预加载文字，不触及 SABR 二进制。 */
(() => {
  const cache = new Map<string, unknown>();
  const emit = (track: unknown): void => window.postMessage({ type: "lexiflow-native-captions", track }, location.origin);
  const ingest = (urlText: string, body: unknown): void => {
    try {
      const url = new URL(urlText, location.href);
      if (url.origin !== location.origin || url.pathname !== "/api/timedtext") return;
      const videoId = url.searchParams.get("v");
      const trackKey = [url.searchParams.get("lang"), url.searchParams.get("kind"), url.searchParams.get("name"), url.searchParams.get("tlang")].join(":");
      if (!videoId || !trackKey || trackKey.length > 128) return;
      const data = typeof body === "string" && body.length <= 2000000 ? JSON.parse(body) : body;
      if (!data || !Array.isArray(data.events) || data.events.length > 10000) return;
      const fragments: { text: string; startMs: number; endMs: number; offsetMs: number | null; windowId: string | null; append: boolean }[] = [];
      for (const event of data.events) {
        if (!Array.isArray(event.segs)) continue;
        const startMs = event.tStartMs, duration = event.dDurationMs;
        // 缺少有效区间时不能确认来源仍在屏幕上，保守回退到 DOM，不能伪造零点或持续时间。
        if (!Number.isSafeInteger(startMs) || startMs < 0 || !Number.isSafeInteger(duration) || duration <= 0 ||
            !Number.isSafeInteger(startMs + duration)) continue;
        let append = Boolean(event.aAppend);
        for (const segment of event.segs) {
          const offsetMs = segment.tOffsetMs ?? null;
          if (typeof segment.utf8 !== "string" || (offsetMs !== null &&
              (!Number.isSafeInteger(offsetMs) || offsetMs < 0 || offsetMs > duration))) continue;
          fragments.push({ text: segment.utf8, startMs, endMs: startMs + duration, offsetMs,
            windowId: event.wWinId == null ? null : String(event.wWinId), append });
          append = true;
        }
      }
      // 只保留播放点附近的有界来源窗口；不将全片字幕传给 isolated world。
      const time = (document.querySelector("video")?.currentTime ?? 0) * 1000;
      const nearby = fragments.filter(fragment => fragment.endMs > time - 30000 && fragment.startMs < time + 120000).slice(0, 2000);
      const track = { videoId, trackKey, fragments: nearby };
      cache.set(trackKey, track); if (cache.size > 4) cache.delete(cache.keys().next().value!);
      emit(track);
    } catch { /* 非 JSON3、未知格式或过大响应不制造字幕元信息。 */ }
  };
  const originalFetch = window.fetch;
  window.fetch = async function (...args: Parameters<typeof fetch>) {
    const response = await originalFetch.apply(this, args);
    try {
      if (response.url.includes("/api/timedtext")) void response.clone().text().then(body => ingest(response.url, body)).catch(() => undefined);
    } catch { /* 旁路采集失败不能改变播放器收到的响应。 */ }
    return response;
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (this: XMLHttpRequest, ...args: unknown[]) {
    this.addEventListener("load", () => {
      try {
        if (this.responseURL.includes("/api/timedtext")) ingest(this.responseURL, this.responseType === "json" ? this.response : this.responseText);
      } catch { /* 二进制响应不读取为字符串。 */ }
    }, { once: true });
    return (originalOpen as (...args: unknown[]) => void).apply(this, args);
  } as typeof XMLHttpRequest.prototype.open;
  window.addEventListener("message", event => {
    if (event.source === window && event.origin === location.origin && event.data?.type === "lexiflow-native-ready") for (const track of cache.values()) emit(track);
  });
  document.addEventListener("yt-navigate-start", () => cache.clear());
})();
