import { BilingualOverlay, type PresentedLine, type RenderedLine } from "./overlay";
import { extractIncrementalText, rememberBounded } from "./caption-debug";
import { snapshotSegments, snapshotText } from "./protocol";
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
let observedSequence = -1;
let terminalSequence = -1;
type TrackedLine = { line: PresentedLine; topicKey: string; videoId: string };
const debugPresented = new Map<string, TrackedLine>();
const debugSent = new Set<string>();
const debugStarted = new Set<string>();
function sendDebug(event: "video-start" | "incremental" | "final" | "interrupted", topicKey: string,
  videoId: string, line: PresentedLine, requestId?: string, text = line.text): void {
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId) || !topicKey || topicKey.length > 128 || line.subtitleKey.length > 128 || text.length > 16384) return;
  const eventId = event === "incremental" && requestId && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(requestId)
    ? requestId : crypto.randomUUID();
  void chrome.runtime.sendMessage({ type: "caption-debug", payload: { eventId, event, topicKey, videoId,
    subtitleKey: line.subtitleKey, positionMs: line.positionMs, text } }).catch(() => undefined);
}
function record(observation: Parameters<Diagnostics["record"]>[0]): void {
  try { diagnostics.record(observation); } catch { /* diagnostics never changes user-visible behavior */ }
}
function updateDiagnostics(): void { try { overlay.updateDiagnostics(diagnostics.snapshot()); } catch { /* diagnostic UI is optional */ } }
let capture: CaptionCapture | undefined;
let lifecycle: PageLifecycle;
function finalizePresented(line: PresentedLine): void {
  const tracked = debugPresented.get(line.subtitleKey);
  if (!tracked) return;
  const prior = tracked.line;
  const view = latestView;
  const keys = new Set(view.event ? snapshotSegments(view.event.request.currentSnapshot).map(segment => segment.key) : []);
  const frozen = new Set(view.frozenKeys ?? []), finalized = new Set(view.finalizedKeys ?? []);
  if (view.event && prior.segmentKeys.every(key => frozen.has(key) || finalized.has(key)))
    sendDebug("final", tracked.topicKey, tracked.videoId, prior, undefined, prior.text);
  debugPresented.delete(line.subtitleKey);
}
function interruptPresented(): void {
  for (const tracked of debugPresented.values())
    sendDebug("interrupted", tracked.topicKey, tracked.videoId, tracked.line, undefined, tracked.line.text);
  debugPresented.clear();
}
const overlay = new BilingualOverlay((entryId, lexiconVersion) => { void updatePreferences("suppress", entryId, lexiconVersion); }, finalizePresented);
function captureLivePage(): void {
  if (lifecycle && lifecycle.videoId !== videoIdFromLocation()) lifecycle.refreshPage();
  overlay.position();
  void capture?.capture();
}

function renderCurrentView(): number {
  const currentCapture = capture;
  const view = latestView.event &&
    snapshotText(latestView.event.request.currentSnapshot) !== currentCapture?.currentCaption()
    ? { state: "idle" as const } : latestView;
  const source = currentCapture?.readSource();
  const shown = overlay.render(view, { ready: preferenceReady, entryKeys: suppressed, message: preferenceMessage },
    source && source.caption.length <= MAX_CAPTION_LENGTH ? source : undefined);
  const rendered = overlay.takePresentation();
  if (source && view.event && preferenceReady) {
    const videoId = lifecycle?.videoId;
    const topic = view.event.request.captionTopicKey;
    const live = new Set(rendered.lines.map(line => line.subtitleKey));
    for (const [key, tracked] of debugPresented) {
      if (live.has(key)) continue;
      finalizePresented(tracked.line);
    }
    for (const line of rendered.lines) if (videoId) debugPresented.set(line.subtitleKey, { line, topicKey: topic, videoId });
    if (videoId && rendered.lines.length && !debugStarted.has(videoId)) {
      sendDebug("video-start", topic, videoId, rendered.lines[0], undefined, ""); rememberBounded(debugStarted, videoId, 16);
    }
    if (view.debugRequestEvent) {
      const responseEvent = view.debugRequestEvent;
      const token = `${responseEvent.sequence}:${view.debugRequestId ?? responseEvent.key}`;
      if (!debugSent.has(token)) {
        rememberBounded(debugSent, token, 64);
        if (videoId) {
          const requestEvent = view.debugRequestEvent;
          const appendedRanges: Array<{ start: number; end: number }> = [];
          let offset = 0;
          for (const group of requestEvent?.request.currentSnapshot.captions ?? []) for (const segment of group.segments) {
            if (segment.append) appendedRanges.push({ start: offset, end: offset + segment.text.length });
            offset += segment.text.length;
          }
          appendedRanges.sort((left, right) => left.start - right.start);
          const mergedRanges: Array<{ start: number; end: number }> = [];
          for (const range of appendedRanges) {
            const last = mergedRanges.at(-1);
            if (last && range.start <= last.end) last.end = Math.max(last.end, range.end);
            else mergedRanges.push({ ...range });
          }
          const actual = rendered.lines.map(line => ({ line, text: extractIncrementalText(line, mergedRanges) }))
            .filter((entry): entry is { line: RenderedLine; text: string } => !!entry.text);
          if (actual.length) {
            const first = actual[0].line;
            const combinedText = actual.map(entry => entry.text).join("\n");
            if (combinedText.length <= 16384) {
              const combined = { ...first, segmentKeys: [...new Set(actual.flatMap(entry => entry.line.segmentKeys))], text: combinedText };
              sendDebug("incremental", topic, videoId, combined, view.debugRequestId, combined.text);
            }
          }
        }
      }
    }
  }
  if (view.state === "ready" && view.event && preferenceReady) {
    if (shown > 0 && lastShownSequence !== view.event.sequence) { lastShownSequence = view.event.sequence; record({ outcome: "shown" }); }
    else if (shown === 0 && (view.hints?.length ?? 0) > 0 && view.hints!.every(hint => suppressed.has(`${hint.lexiconEntryId}@${hint.lexiconVersion}`)) && lastSuppressedSequence !== view.event.sequence) { lastSuppressedSequence = view.event.sequence; record({ outcome: "suppressed" }); }
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
  renderCurrentView(); updateDiagnostics();
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
      record({ outcome: "stale-at-render" }); updateDiagnostics(); return;
    }
    const started = performance.now(); latestView = view; renderCurrentView();
    record({ stage: "render", elapsedMs: performance.now() - started });
    if (["ready", "no-pending", "fallback"].includes(view.state) && view.event && terminalSequence !== view.event.sequence && observedSequence === view.event.sequence) {
      terminalSequence = view.event.sequence; record({ stage: "endToEnd", elapsedMs: performance.now() - observedAt });
    }
    updateDiagnostics();
  }, globalThis, value => { record(value); updateDiagnostics(); });

lifecycle = createPageLifecycle({
  currentVideoId: videoIdFromLocation,
  hasPlayer: () => !!document.querySelector(".html5-video-player, #movie_player"),
  hasCaptionMotion: () => !!document.querySelector("#ytp-caption-window-container")?.getAnimations({ subtree: true })
    .some(animation => animation.playState === "running" || animation.pending)
}, () => {
  capture?.invalidate();
}, captureLivePage, reason => {
  if (reason) {
    if (reason !== "interrupted") record({ outcome: reason });
    interruptPresented(); debugSent.clear(); debugStarted.clear();
  }
  renderCurrentView(); updateDiagnostics();
});
capture = new CaptionCapture(lifecycle, coordinator, {
  sha256, now: () => performance.now(),
  onWaiting: () => { latestView = { state: "waiting" }; renderCurrentView(); },
  onSource: source => { if (source) renderCurrentView(); else overlay.render({ state: "idle" },
    { ready: preferenceReady, entryKeys: suppressed, message: preferenceMessage }); },
  onObserved: sequence => { observedAt = performance.now(); observedSequence = sequence; record({ outcome: "observed" }); },
  onOversized: () => record({ outcome: "oversized" }),
  onAcquisition: elapsedMs => record({ stage: "acquisition", elapsedMs }),
  onObservation: outcome => record({ outcome }),
  onInterrupted: interruptPresented
});

renderCurrentView();
void updatePreferences("read");
chrome.runtime.onMessage?.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "page-enhancement") return;
  if (lifecycle.videoId !== videoIdFromLocation()) lifecycle.refreshPage();
  if (!videoIdFromLocation()) { respond({ ok: false, reason: "not-video-page" }); return; }
  if (lifecycle.navigating || !document.querySelector(".html5-video-player, #movie_player")) {
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
