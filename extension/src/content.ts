import { BilingualOverlay, OVERLAY_ID as overlayId, visibleSegments } from "./overlay";
import { readCaptionSource } from "./caption-source";
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

async function contentId(videoId: string): Promise<string> {
  const digest = await sha256(`youtube\u0000${videoId}`);
  return `${digest.slice(0, 8)}-${digest.slice(8, 12)}-5${digest.slice(13, 16)}-8${digest.slice(17, 20)}-${digest.slice(20, 32)}`;
}

// Clipping preserves source geometry and visibility detection without changing subtitle nodes.
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
  const view = latestView.state === "ready" && latestView.event?.request.caption !== currentCaption()
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
    // Re-read the live source at delivery, not merely the last debounced snapshot.
    if (view.state === "ready" && (!view.event || view.event.sequence !== sourceSequence ||
        view.event.request.caption !== currentCaption() || activeVideoId !== videoIdFromLocation())) {
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
  return readCaptionSource(visibleSegments(player));
}

function currentCaption(): string | undefined {
  return currentSource()?.caption;
}

function scheduleCapture(): void {
  // MutationObserver runs before paint: invalidate old hints before hashing or request debounce.
  void captureCurrentCaption();
}

async function captureCurrentCaption(): Promise<void> {
  if (pageVideoId !== videoIdFromLocation()) resetPageSetting();
  const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
  if (activePlayer !== player || (player !== null && !document.getElementById(overlayId))) {
    activePlayer = player;
    activeCaptionKey = undefined;
    sourceRevision += 1;
    coordinator.clear(++sourceSequence);
  }
  overlay.position();
  const video = document.querySelector<HTMLVideoElement>("video");
  const videoId = videoIdFromLocation();
  const caption = currentCaption();
  if (video === null || videoId === undefined || caption === undefined) {
    if (activeCaptionKey !== undefined) {
      activeCaptionKey = undefined;
      coordinator.clear(++sourceSequence);
    }
    return;
  }
  if (activeVideoId !== videoId) {
    activeVideoId = videoId;
    sourceRevision += 1;
    activeCaptionKey = undefined;
  }
  const videoTimeMs = Math.max(0, Math.floor((Number.isFinite(video.currentTime) ? video.currentTime : 0) * 1_000));
  const captionKey = `${videoId}\u0000${sourceRevision}\u0000${caption}`;
  const nextLayoutKey = JSON.stringify(currentSource()?.lineBreaks ?? []);
  if (captionKey === activeCaptionKey) {
    if (nextLayoutKey !== layoutKey) { layoutKey = nextLayoutKey; renderCurrentView(); }
    return;
  }
  layoutKey = nextLayoutKey;
  activeCaptionKey = captionKey;
  observedAt = performance.now();
  diagnostics.record({ outcome: "observed" });
  coordinator.clear(++sourceSequence);
  const sequence = ++sourceSequence;
  if (caption.length > MAX_CAPTION_LENGTH) {
    diagnostics.record({ outcome: "oversized" });
    coordinator.clear(sequence);
    return;
  }
  let id: string;
  let segmentId: string;
  try {
    id = await contentId(videoId);
    segmentId = await sha256(`${id}\u0000${sourceRevision}\u0000${sequence}\u0000${videoTimeMs}\u0000${caption}`);
  } catch {
    if (sequence === sourceSequence) coordinator.clear(++sourceSequence);
    return;
  }
  if (sequence !== sourceSequence || currentCaption() !== caption || videoIdFromLocation() !== videoId) {
    diagnostics.record({ outcome: "cancelled-acquisition" });
    overlay.updateDiagnostics(diagnostics.snapshot());
    return;
  }
  const request: CaptionHintRequest = {
    contentId: id,
    contentRevision: sourceRevision,
    segmentId,
    caption,
    startOffset: 0,
    endOffset: caption.length
  };
  diagnostics.record({ stage: "acquisition", elapsedMs: performance.now() - observedAt });
  const event: CaptionEvent = { key: captionKey, sequence, videoTimeMs, request };
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
    coordinator.clear(++sourceSequence);
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
  coordinator.clear(++sourceSequence);
}

function resetPageSetting(): void {
  enhancementEnabled = true;
  pageKey = crypto.randomUUID();
  pageVideoId = videoIdFromLocation();
  sourceStopped = false;
  activeCaptionKey = undefined;
}

document.addEventListener("yt-navigate-start", () => { navigating = true; resetForNavigation(); });
document.addEventListener("yt-navigate-finish", () => { navigating = false; scheduleCapture(); });
window.addEventListener("popstate", () => { resetForNavigation(); scheduleCapture(); });
window.addEventListener("resize", scheduleCapture);
document.addEventListener("timeupdate", scheduleCapture, true);

document.addEventListener("seeking", () => {
  seeking = true;
  sourceRevision += 1;
  activeCaptionKey = undefined;
  coordinator.clear(++sourceSequence);
}, true);
document.addEventListener("seeked", () => { seeking = false; sourceStopped = false; scheduleCapture(); }, true);
document.addEventListener("play", () => { sourceStopped = false; scheduleCapture(); }, true);
for (const name of ["emptied", "ended"]) {
  document.addEventListener(name, () => {
    sourceStopped = true;
    activeCaptionKey = undefined;
    coordinator.clear(++sourceSequence);
  }, true);
}

// A hidden tab must not keep displaying or requesting an obsolete caption.
document.addEventListener("visibilitychange", scheduleCapture);
