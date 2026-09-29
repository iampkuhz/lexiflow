import { BilingualOverlay } from "./overlay";
import { snapshotText } from "./protocol";
import { PREFERENCE_KEY, type PreferenceAction, type PreferenceResult } from "./preferences";
import { Diagnostics } from "./diagnostics";
import { MAX_CAPTION_LENGTH, type ApiResult } from "./protocol";
import { CaptionStreamCoordinator, type CaptionEvent, type StreamView } from "./stream";
import { createPageLifecycle, type PageLifecycle } from "./page-lifecycle";
import { CaptionCapture } from "./caption-capture";

const diagnostics = new Diagnostics();
function videoIdFromLocation(): string | undefined {
  const value = new URL(location.href).searchParams.get("v")?.trim();
  return value && value.length <= 256 ? value : undefined;
}
async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}
const sourceMask = document.createElement("style");
sourceMask.textContent = ".lexiflow-inline-active #ytp-caption-window-container{clip-path:inset(50%)!important}";
document.documentElement.append(sourceMask);

let preferenceReady = false;
let suppressed = new Set<string>();
let preferenceMessage = "正在读取本机偏好，英文不受影响。";
let latestView: StreamView = { state: "idle" };
let lastShownSequence = -1;
let lastSuppressedSequence = -1;
let observedAt = performance.now();
const overlay = new BilingualOverlay((entryId, lexiconVersion) => { void updatePreferences("suppress", entryId, lexiconVersion); });
let capture: CaptionCapture | undefined;
let lifecycle: PageLifecycle;
function captureLivePage(): void {
  if (lifecycle && lifecycle.videoId !== videoIdFromLocation()) lifecycle.refreshPage();
  overlay.position();
  void capture?.capture();
}

function renderCurrentView(): number {
  const currentCapture = capture;
  const view = latestView.state === "ready" && latestView.event &&
    snapshotText(latestView.event.request.currentSnapshot) !== currentCapture?.currentCaption()
    ? { state: "idle" as const } : latestView;
  const source = currentCapture?.readSource();
  const shown = overlay.render(view, { ready: preferenceReady, entryKeys: suppressed, message: preferenceMessage },
    source && source.caption.length <= MAX_CAPTION_LENGTH ? source : undefined);
  if (view.state === "ready" && view.event && preferenceReady) {
    if (shown > 0 && lastShownSequence !== view.event.sequence) { lastShownSequence = view.event.sequence; diagnostics.record({ outcome: "shown" }); }
    else if (shown === 0 && lastSuppressedSequence !== view.event.sequence) { lastSuppressedSequence = view.event.sequence; diagnostics.record({ outcome: "suppressed" }); }
  }
  return shown;
}
async function updatePreferences(action: PreferenceAction, entryId?: string, lexiconVersion?: number): Promise<void> {
  let result: PreferenceResult;
  try { result = await chrome.runtime.sendMessage({ type: "local-preferences", action, entryId, lexiconVersion }); }
  catch { result = { ok: false, reason: "storage" }; }
  if (result?.ok && Array.isArray(result.entryKeys)) { suppressed = new Set(result.entryKeys); preferenceReady = true; preferenceMessage = ""; }
  else preferenceMessage = result && !result.ok && result.reason === "limit"
    ? "本机抑制已达上限，请先恢复提示。" : "本机偏好读取或保存失败；未宣称已保存。";
  renderCurrentView(); overlay.updateDiagnostics(diagnostics.snapshot());
}

const coordinator = new CaptionStreamCoordinator(
  (event, requestId) => ({
    promise: chrome.runtime.sendMessage({ type: "caption-hints", requestId, payload: event.request })
      .catch(() => ({ ok: false, reason: "network" } as ApiResult)),
    cancel: () => { void chrome.runtime.sendMessage({ type: "cancel-caption-hint", requestId }).catch(() => undefined); }
  }),
  view => {
    const liveCaption = capture?.currentCaption();
    if (view.state === "ready" && (!view.event || !capture?.isCurrentSequence(view.event.sequence) ||
        (liveCaption !== undefined && snapshotText(view.event.request.currentSnapshot) !== liveCaption) ||
        lifecycle.videoId !== videoIdFromLocation() || !lifecycle.enabled || document.hidden)) {
      diagnostics.record({ outcome: "stale-at-render" }); overlay.updateDiagnostics(diagnostics.snapshot()); return;
    }
    const started = performance.now(); latestView = view; renderCurrentView();
    diagnostics.record({ stage: "render", elapsedMs: performance.now() - started });
    if (["ready", "no-pending", "fallback"].includes(view.state)) diagnostics.record({ stage: "endToEnd", elapsedMs: performance.now() - observedAt });
    overlay.updateDiagnostics(diagnostics.snapshot());
  }, globalThis, value => { diagnostics.record(value); overlay.updateDiagnostics(diagnostics.snapshot()); });

lifecycle = createPageLifecycle({
  currentVideoId: videoIdFromLocation,
  hasPlayer: () => !!document.querySelector(".html5-video-player, #movie_player"),
  hasCaptionMotion: () => !!document.querySelector("#ytp-caption-window-container")?.getAnimations({ subtree: true })
    .some(animation => animation.playState === "running" || animation.pending)
}, () => {
  capture?.invalidate();
}, captureLivePage, () => {
  renderCurrentView(); overlay.updateDiagnostics(diagnostics.snapshot());
});
capture = new CaptionCapture(lifecycle, coordinator, {
  sha256, now: () => performance.now(),
  onWaiting: () => { latestView = { state: "waiting" }; renderCurrentView(); },
  onSource: source => { if (source) renderCurrentView(); else overlay.render({ state: "idle" },
    { ready: preferenceReady, entryKeys: suppressed, message: preferenceMessage }); },
  onObserved: () => { observedAt = performance.now(); diagnostics.record({ outcome: "observed" }); },
  onOversized: () => diagnostics.record({ outcome: "oversized" }),
  onAcquisition: elapsedMs => diagnostics.record({ stage: "acquisition", elapsedMs })
});

renderCurrentView();
void updatePreferences("read");
chrome.runtime.onMessage?.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "page-enhancement") return;
  if (lifecycle.videoId !== videoIdFromLocation()) lifecycle.refreshPage();
  if (!videoIdFromLocation() || lifecycle.navigating || !document.querySelector(".html5-video-player, #movie_player")) {
    respond({ ok: false }); return;
  }
  if (message.action === "set") {
    if (!lifecycle.setEnabled(message.pageKey, message.enabled)) { respond({ ok: false }); return; }
    captureLivePage();
  } else if (message.action !== "read") { respond({ ok: false }); return; }
  respond({ ok: true, enabled: lifecycle.enabled, pageKey: lifecycle.pageKey });
});
chrome.storage?.onChanged.addListener((changes, area) => { if (area === "local" && changes[PREFERENCE_KEY]) void updatePreferences("read"); });
window.addEventListener("message", event => {
  if (event.source === window && event.origin === location.origin && event.data?.type === "lexiflow-native-captions") {
    capture?.acceptNativeTrack(event.data.track, videoIdFromLocation()); captureLivePage();
  }
});
window.postMessage({ type: "lexiflow-native-ready" }, location.origin);
lifecycle.attach();
