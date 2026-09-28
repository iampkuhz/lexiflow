import { BilingualOverlay, OVERLAY_ID as overlayId } from "./overlay";
import { CaptionViewport, readCaptionSource } from "./caption-source";
import { CaptionSnapshotTracker } from "./caption-snapshot";
import { YoutubeSourceMetadata } from "./youtube-source";
import { snapshotText } from "./protocol";
const snapshotTracker = new CaptionSnapshotTracker();
const captionViewport = new CaptionViewport();
const sourceMetadata = new YoutubeSourceMetadata();
let trackKey: string | null = null;
let lastKnownTrackKey: string | null = null;
let nativeIdentity = "";
let topic: string | undefined;
let topicPromise: Promise<string> | undefined;
window.addEventListener("message", event => {
  if (event.source === window && event.origin === location.origin && event.data?.type === "lexiflow-native-captions") {
    sourceMetadata.accept(event.data.track, videoIdFromLocation());
    scheduleCapture();
  }
});
window.postMessage({ type: "lexiflow-native-ready" }, location.origin);
import { PREFERENCE_KEY, type PreferenceAction, type PreferenceResult } from "./preferences";
import { Diagnostics } from "./diagnostics";
import {
  MAX_CAPTION_LENGTH,
  type ApiResult,
  type CaptionHintRequest
} from "./protocol";
import {
  CaptionStreamCoordinator,
  type CaptionEvent,
  type StreamView
} from "./stream";

const diagnostics = new Diagnostics();

function videoIdFromLocation(): string | undefined {
  const value = new URL(location.href).searchParams.get("v")?.trim();
  return value && value.length <= 256 ? value : undefined;
}

function bytesToHex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256(value: string): Promise<string> {
  return bytesToHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

// 裁剪只影响绘制，保留来源几何信息与可见性检测，不修改字幕节点。
const sourceMask = document.createElement("style");
sourceMask.textContent = ".lexiflow-inline-active #ytp-caption-window-container{clip-path:inset(50%)!important}";
document.documentElement.append(sourceMask);

let preferenceReady = false;
let suppressed = new Set<string>();
let preferenceMessage = "正在读取本机偏好，英文不受影响。";
let latestView: StreamView = { state: "idle" };
let lastShownSequence = -1;
let lastSuppressedSequence = -1;
let enhancementEnabled = true;
let pageKey = crypto.randomUUID();
let pageVideoId = videoIdFromLocation();
let layoutKey = "";
let missingCaptionAt: number | undefined;
const overlay = new BilingualOverlay(
  (entryId, lexiconVersion) => { void updatePreferences("suppress", entryId, lexiconVersion); },
  () => { void updatePreferences("restore-all"); },
  () => { diagnostics.reset(); overlay.updateDiagnostics(diagnostics.snapshot()); }
);

async function updatePreferences(action: PreferenceAction, entryId?: string, lexiconVersion?: number): Promise<void> {
  let result: PreferenceResult;
  try { result = await chrome.runtime.sendMessage({ type: "local-preferences", action, entryId, lexiconVersion }); }
  catch { result = { ok: false, reason: "storage" }; }
  if (result?.ok && Array.isArray(result.entryKeys)) {
    suppressed = new Set(result.entryKeys);
    preferenceReady = true;
    preferenceMessage = "";
  } else {
    preferenceMessage = result && !result.ok && result.reason === "limit" ? "本机抑制已达上限，请先恢复提示。" : "本机偏好读取或保存失败；未宣称已保存。";
  }
  renderCurrentView();
  overlay.updateDiagnostics(diagnostics.snapshot());
}

function renderCurrentView(): number {
  const view = latestView.state === "ready" && (latestView.event && snapshotText(latestView.event.request.currentSnapshot)) !== currentCaption()
    ? { state: "idle" as const } : latestView;
  const source = currentSource();
  const shown = overlay.render(view, { ready: preferenceReady, entryKeys: suppressed, message: preferenceMessage },
    source && source.caption.length <= MAX_CAPTION_LENGTH ? source : undefined);
  if (view.state === "ready" && view.event && preferenceReady) {
    if (shown > 0 && lastShownSequence !== view.event.sequence) {
      lastShownSequence = view.event.sequence;
      diagnostics.record({ outcome: "shown" });
    } else if (shown === 0 && lastSuppressedSequence !== view.event.sequence) {
      lastSuppressedSequence = view.event.sequence;
      diagnostics.record({ outcome: "suppressed" });
    }
  }
  return shown;
}
const coordinator = new CaptionStreamCoordinator(
  (event, requestId) => ({
    promise: chrome.runtime
      .sendMessage({ type: "caption-hints", requestId, payload: event.request })
      .catch(() => ({ ok: false, reason: "network" } as ApiResult)),
    cancel: () => {
      void chrome.runtime.sendMessage({ type: "cancel-caption-hint", requestId }).catch(() => undefined);
    }
  }),
  (view) => {
    // 交付结果时重新读取实时来源，不能只相信最后一次 debounce snapshot。
    const liveCaption = currentCaption();
    if (view.state === "ready" && (!view.event || view.event.sequence !== sourceSequence ||
        (liveCaption !== undefined && snapshotText(view.event.request.currentSnapshot) !== liveCaption) ||
        activeVideoId !== videoIdFromLocation())) {
      diagnostics.record({ outcome: "stale-at-render" });
      overlay.updateDiagnostics(diagnostics.snapshot());
      return;
    }
    const started = performance.now();
    latestView = view;
    renderCurrentView();
    diagnostics.record({ stage: "render", elapsedMs: performance.now() - started });
    if (view.state === "ready" || view.state === "no-pending" || view.state === "fallback") {
      diagnostics.record({ stage: "endToEnd", elapsedMs: performance.now() - observedAt });
    }
    overlay.updateDiagnostics(diagnostics.snapshot());
  },
  globalThis,
  value => { diagnostics.record(value); overlay.updateDiagnostics(diagnostics.snapshot()); }
);

let sourceSequence = 0;
let sourceRevision = 0;
let activeVideoId: string | undefined;
let activePlayer: HTMLElement | null = null;
let activeCaptionKey: string | undefined;
let observedAt = performance.now();
let seeking = false;
let navigating = false;
let sourceStopped = false;

function currentSource() {
  const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
  const video = player?.querySelector<HTMLVideoElement>("video");
  if (!enhancementEnabled || player === null || !video || video.ended || sourceStopped || document.hidden || seeking || navigating || player.classList.contains("ad-showing")) return undefined;
  return readCaptionSource(captionViewport.read(player));
}

function currentCaption(): string | undefined {
  return currentSource()?.caption;
}

function scheduleCapture(): void {
  // 来源文字即时整理，异步响应不得清空仍有效的提示。
  void captureCurrentCaption();
}

function clearSource(): void {
  missingCaptionAt = undefined;
  captionViewport.reset();
  snapshotTracker.reset();
  coordinator.clear(++sourceSequence);
}

async function captureCurrentCaption(): Promise<void> {
  if (pageVideoId !== videoIdFromLocation()) resetPageSetting();
  const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
  if (activePlayer !== player || (player !== null && !document.getElementById(overlayId))) {
    activePlayer = player;
    activeCaptionKey = undefined;
    sourceRevision += 1;
    clearSource();
  }
  overlay.position();
  const video = document.querySelector<HTMLVideoElement>("video");
  const videoId = videoIdFromLocation();
  const caption = currentCaption();
  if (video === null || videoId === undefined || caption === undefined) {
    const nativeContainer = player?.querySelector<HTMLElement>("#ytp-caption-window-container");
    const transientGap = caption === undefined && video && !video.ended && videoId && enhancementEnabled &&
      !document.hidden && !seeking && !navigating && !sourceStopped && !player?.classList.contains("ad-showing") &&
      nativeContainer?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    if (transientGap) {
      missingCaptionAt ??= performance.now();
      overlay.render({ state: "idle" }, { ready: preferenceReady, entryKeys: suppressed, message: preferenceMessage });
    } else if (activeCaptionKey !== undefined) {
      activeCaptionKey = undefined;
      clearSource();
    }
    return;
  }
  const wasMissing = missingCaptionAt !== undefined;
  if (wasMissing && performance.now() - missingCaptionAt! > 650) {
    activeCaptionKey = undefined;
    clearSource();
  }
  missingCaptionAt = undefined;
  if (activeVideoId !== videoId) {
    activeVideoId = videoId;
    sourceRevision += 1;
    activeCaptionKey = undefined;
  }
  const videoTimeMs = Math.max(0, Math.floor((Number.isFinite(video.currentTime) ? video.currentTime : 0) * 1_000));
  const source = currentSource();
  if (!source) return;
  const metadata = sourceMetadata.match(source.caption, videoTimeMs);
  const firstWord = source.caption.match(/\S+/u)?.[0] ?? source.caption;
  const firstMetadata = metadata.metadata(firstWord, 0);
  const nextNativeIdentity = metadata.trackKey && firstMetadata.startMs !== null
    ? `${metadata.trackKey}:${firstMetadata.windowId}:${firstMetadata.startMs}:${firstMetadata.offsetMs}` : "";
  const textUnchanged = activeCaptionKey === `${videoId}\u0000${sourceRevision}\u0000${caption}`;
  // JSON3 的有效时间窗与 DOM roll-up 并非同步：短暂无法匹配不是换轨证据。
  if ((metadata.trackKey !== null && lastKnownTrackKey !== null && metadata.trackKey !== lastKnownTrackKey) ||
      (textUnchanged && nativeIdentity && nextNativeIdentity && nativeIdentity !== nextNativeIdentity)) {
    clearSource(); activeCaptionKey = undefined;
  }
  if (metadata.trackKey !== null) lastKnownTrackKey = metadata.trackKey;
  if (nextNativeIdentity) nativeIdentity = nextNativeIdentity;
  trackKey = metadata.trackKey;
  const captionKey = `${videoId}\u0000${sourceRevision}\u0000${caption}`;
  const nextLayoutKey = JSON.stringify(currentSource()?.lineBreaks ?? []);
  if (captionKey === activeCaptionKey) {
    if (nextLayoutKey !== layoutKey || wasMissing) { layoutKey = nextLayoutKey; renderCurrentView(); }
    return;
  }
  layoutKey = nextLayoutKey;
  activeCaptionKey = captionKey;
  observedAt = performance.now();
  diagnostics.record({ outcome: "observed" });
  const sequence = ++sourceSequence;
  if (caption.length > MAX_CAPTION_LENGTH) {
    diagnostics.record({ outcome: "oversized" });
    clearSource();
    return;
  }
  const snapshot = snapshotTracker.capture(source, metadata.metadata);
  // 主题哈希只在导航时改变。未完成时先显示英文，不阻塞源遮罩和绘制。
  if (!topic) {
    latestView = { state: "waiting" };
    renderCurrentView();
    const expectedVideo = videoId;
    try {
      const resolved = await (topicPromise ??= sha256(`youtube\u0000${videoId}`));
      if (videoIdFromLocation() !== expectedVideo) return;
      topic = resolved;
    } catch { topicPromise = undefined; activeCaptionKey = undefined; return; }
    if (sequence !== sourceSequence) { activeCaptionKey = undefined; scheduleCapture(); return; }
  }
  const request: CaptionHintRequest = {
    captionTopicKey: topic, trackKey, lastRequestedSnapshot: null, currentSnapshot: snapshot
  };
  diagnostics.record({ stage: "acquisition", elapsedMs: performance.now() - observedAt });
  const event: CaptionEvent = { key: captionKey, sequence: sourceSequence, videoTimeMs, request };
  coordinator.submit(event);
}

renderCurrentView();
void updatePreferences("read");
chrome.runtime.onMessage?.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "page-enhancement") return;
  if (pageVideoId !== videoIdFromLocation()) resetPageSetting();
  if (!videoIdFromLocation() || navigating || !document.querySelector(".html5-video-player, #movie_player")) {
    respond({ ok: false }); return;
  }
  if (message.action === "set") {
    if (message.pageKey !== pageKey || typeof message.enabled !== "boolean") { respond({ ok: false }); return; }
    enhancementEnabled = message.enabled;
    activeCaptionKey = undefined;
    clearSource();
    scheduleCapture();
  } else if (message.action !== "read") { respond({ ok: false }); return; }
  respond({ ok: true, enabled: enhancementEnabled, pageKey });
});
chrome.storage?.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[PREFERENCE_KEY]) void updatePreferences("read");
});
scheduleCapture();
new MutationObserver(scheduleCapture).observe(document.documentElement, {
  childList: true,
  subtree: true,
  characterData: true,
  attributes: true,
  attributeFilter: ["class", "style", "hidden", "aria-hidden"]
});
function resetForNavigation(): void {
  resetPageSetting();
  activeVideoId = undefined;
  activeCaptionKey = undefined;
  clearSource();
}

function resetPageSetting(): void {
  enhancementEnabled = true;
  pageKey = crypto.randomUUID();
  pageVideoId = videoIdFromLocation();
  topic = undefined; topicPromise = undefined; trackKey = null; lastKnownTrackKey = null; nativeIdentity = ""; sourceMetadata.reset();
  sourceStopped = false;
  activeCaptionKey = undefined;
}

document.addEventListener("yt-navigate-start", () => { navigating = true; resetForNavigation(); });
document.addEventListener("yt-navigate-finish", () => { navigating = false; scheduleCapture(); });
window.addEventListener("popstate", () => { resetForNavigation(); scheduleCapture(); });
window.addEventListener("resize", scheduleCapture);
document.addEventListener("timeupdate", scheduleCapture, true);

// YouTube roll-up 通过 CSS transform 移入新行，动画中没有持续 DOM mutation。
// 仅在原生字幕动画运行时逐帧重读几何；不固定轮询，也不让英文等待 timeupdate。
let motionFrame: number | undefined;
let motionDeadline = 0;
function captureCaptionMotion(): void {
  motionFrame = undefined;
  if (document.hidden) return;
  scheduleCapture();
  const source = document.querySelector("#ytp-caption-window-container");
  if (performance.now() < motionDeadline && source?.getAnimations({ subtree: true })
      .some(animation => animation.playState === "running" || animation.pending)) {
    motionFrame = requestAnimationFrame(captureCaptionMotion);
  }
}
function isCaptionMotion(event: Event): boolean {
  return event.target instanceof Element && !!event.target.closest("#ytp-caption-window-container");
}
for (const name of ["transitionrun", "animationstart"]) document.addEventListener(name, event => {
  if (!isCaptionMotion(event)) return;
  motionDeadline = performance.now() + 2000;
  if (motionFrame === undefined) motionFrame = requestAnimationFrame(captureCaptionMotion);
}, true);
for (const name of ["transitionend", "transitioncancel", "animationend", "animationcancel"]) {
  document.addEventListener(name, event => { if (isCaptionMotion(event)) scheduleCapture(); }, true);
}


document.addEventListener("seeking", () => {
  seeking = true;
  sourceRevision += 1;
  activeCaptionKey = undefined;
  clearSource();
}, true);
document.addEventListener("seeked", () => { seeking = false; sourceStopped = false; scheduleCapture(); }, true);
document.addEventListener("play", () => { sourceStopped = false; scheduleCapture(); }, true);
for (const name of ["emptied", "ended"]) {
  document.addEventListener(name, () => {
    sourceStopped = true;
    activeCaptionKey = undefined;
    clearSource();
  }, true);
}

// 隐藏的标签页不得继续显示或请求过期字幕。
document.addEventListener("visibilitychange", scheduleCapture);
