import { isManagementUrl } from "./manage-context";
import { BilingualOverlay, type PresentedLine, type RenderedLine } from "./overlay";
import { extractIncrementalText, rememberBounded } from "./caption-debug";
import { MAX_CAPTION_LENGTH, MAX_PRESENTED_ENTRY_IDS, snapshotText, type ApiResult } from "./protocol";
import { parsePreferences, type PreferenceAction, type PreferenceResult } from "./preferences";
import { Diagnostics } from "./diagnostics";
import { CaptionStreamCoordinator, type StreamView } from "./stream";
import { createPageLifecycle, type PageLifecycle } from "./page-lifecycle";
import { CaptionCapture } from "./caption-capture";

import { CAPTION_LINE_LIMIT_KEY, captionLineLimit } from "./caption-display";
import { TEST_CHANNEL } from "./backend-target";
import { HintVocabulary } from "./hint-vocabulary";

// 两个隔离世界共享同一 DOM；同步认领页面，避免两套脚本同时接管播放器。
const owner = TEST_CHANNEL ? "test" : "formal";
const marker = "data-lexiflow-caption-owner";
const root = document.documentElement;
const extensionId = globalThis.chrome?.runtime?.id;
if (extensionId && !root.hasAttribute(marker)) {
  root.setAttribute(marker, owner);
  startEnhancement();
} else if (extensionId) {
  chrome.runtime.onMessage?.addListener((message, sender, respond) => {
    if (sender.id === extensionId && message?.type === "page-enhancement")
      respond({ ok: false, reason: "other-channel" });
  });
}

function startEnhancement(): void {
  const diagnostics = new Diagnostics();
  const vocabulary = new HintVocabulary();
  let detached = false;
  const listeners = new AbortController();
  function detach(): void {
    if (detached) return;
    detached = true;
    // A send may fail inside a render or stream notification; teardown once that stack unwinds.
    queueMicrotask(() => {
      listeners.abort();
      lifecycle.dispose();
      debugPresented.clear(); debugSent.clear(); debugStarted.clear();
      overlay.dispose();
      sourceMask.remove();
      if (root.getAttribute(marker) === owner) root.removeAttribute(marker);
    });
  }
  function canCommunicate(): boolean {
    if (detached) return false;
    const runtime = globalThis.chrome?.runtime;
    if (runtime?.id && runtime.id === extensionId && typeof runtime.sendMessage === "function") return true;
    detach(); return false;
  }
  async function send<T>(message: object, fallback: T): Promise<T> {
    try {
      if (!canCommunicate()) return fallback;
      return await chrome.runtime.sendMessage(message) ?? fallback;
    } catch (error) {
      if (String(error).includes("Extension context invalidated")) detach();
      return fallback;
    }
  }
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
  let connectionRevision = 0;
  let connectionSwitching = false;
  let displayedHints = 0;
  let preferenceRetryAt = 0;
  let preferenceRecovery: Promise<void> | undefined;
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
    const positionMs = line.positionMs ?? Math.floor(document.querySelector("video")?.currentTime! * 1000);
    if (!/^[A-Za-z0-9_-]{11}$/.test(videoId) || !topicKey || topicKey.length > 128 || line.subtitleKey.length > 128 ||
        !Number.isSafeInteger(positionMs) || positionMs < 0 || text.length > 16384) return;
    const eventId = event === "incremental" && requestId && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(requestId)
      ? requestId : crypto.randomUUID();
    void send({ type: "caption-debug", payload: { eventId, event, topicKey, videoId,
      subtitleKey: line.subtitleKey, positionMs, text } }, undefined);
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
  const overlay = new BilingualOverlay(entryId => { void updatePreferences("hide", { entryId }); }, finalizePresented,
    (segments, terms) => { if (lifecycle?.enabled && !lifecycle.navigating) vocabulary.observe(segments, terms); });
  function captureLivePage(): void {
    if (!canCommunicate()) return;
    if (lifecycle && lifecycle.videoId !== videoIdFromLocation()) lifecycle.refreshPage();
    overlay.position();
    if (overlay.needsLayout()) renderCurrentView();
    if (!connectionSwitching) void capture?.capture();
  }

  function renderCurrentView(): number {
    if (!canCommunicate()) return 0;
    const source = capture?.readSource();
    if (!source && overlay.bridgingGap) return 0;
    const view = latestView.event &&
      snapshotText(latestView.event.request.currentSnapshot) !== source?.caption
      ? { state: "idle" as const } : latestView;
    const shown = overlay.render(view, { ready: preferenceReady, hiddenIds: suppressed, message: preferenceMessage },
      source && source.caption.length <= MAX_CAPTION_LENGTH ? source : undefined);
    displayedHints = shown;
    const rendered = overlay.takePresentation();
    for (let start = 0; start < rendered.hintEntryIds.length; start += MAX_PRESENTED_ENTRY_IDS) {
      const entryIds = rendered.hintEntryIds.slice(start, start + MAX_PRESENTED_ENTRY_IDS);
      void send({ type: "local-presented", entryIds }, undefined);
    }
    if (source && view.event && preferenceReady) {
      const videoId = lifecycle?.videoId;
      const topic = view.event.request.captionTopicKey;
      const live = new Set(rendered.lines.map(line => line.subtitleKey));
      for (const [key, tracked] of debugPresented) {
        if (live.has(key)) continue;
        finalizePresented(tracked.line);
      }
      for (const line of rendered.lines) if (videoId) {
        debugPresented.set(line.subtitleKey, { line, topicKey: topic, videoId });
        while (debugPresented.size > 64) {
          const oldest = debugPresented.values().next().value as TrackedLine | undefined;
          if (!oldest) break;
          finalizePresented(oldest.line);
        }
      }
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
      else if (shown === 0 && (view.hints?.length ?? 0) > 0 && view.hints!.every(hint => suppressed.has(hint.lexiconEntryId)) && lastSuppressedSequence !== view.event.sequence) { lastSuppressedSequence = view.event.sequence; record({ outcome: "suppressed" }); }
    }
    return shown;
  }
  async function updatePreferences(action: PreferenceAction, data: { entryId?: string; lemma?: string; gloss?: string; commonWordCutoff?: number } = {}): Promise<void> {
    const revision = connectionRevision;
    const result = await send<PreferenceResult>({ type: "local-preferences", action, ...data }, { ok: false, reason: "storage" });
    if (detached || revision !== connectionRevision) return;
    const state = result?.ok ? parsePreferences(result) : undefined;
    if (state) { suppressed = new Set(state.hidden.map(item => item.entryId)); preferenceReady = true; preferenceMessage = ""; }
    else { preferenceReady = false; suppressed.clear(); preferenceMessage = "本机偏好读取或保存失败；未宣称已保存。"; }
    renderCurrentView(); updateDiagnostics();
  }

  // 页面可能比本机服务先打开。查词恢复后重新读取偏好，不要求用户刷新，也不重发当前字幕。
  function recoverPreferences(): void {
    if (preferenceReady || connectionSwitching || preferenceRecovery || performance.now() < preferenceRetryAt) return;
    preferenceRetryAt = performance.now() + 1000;
    const pending = updatePreferences("read");
    preferenceRecovery = pending;
    void pending.finally(() => { if (preferenceRecovery === pending) preferenceRecovery = undefined; });
  }

  const coordinator = new CaptionStreamCoordinator(
    (payload, requestId) => ({
      promise: send<ApiResult>({ type: "caption-hints", requestId, payload }, { ok: false, reason: "network" }),
      cancel: () => { void send({ type: "cancel-caption-hint", requestId }, undefined); }
    }),
    view => {
      if (detached) return;
      const liveCaption = capture?.currentCaption();
      if (view.state === "ready" && (!view.event || !capture?.isCurrentSequence(view.event.sequence) ||
          (liveCaption !== undefined && snapshotText(view.event.request.currentSnapshot) !== liveCaption) ||
          lifecycle.videoId !== videoIdFromLocation() || !lifecycle.enabled || document.hidden)) {
        record({ outcome: "stale-at-render" }); updateDiagnostics(); return;
      }
      if (view.state === "idle" && !view.event) {
        // stream.clear marks a media/source discontinuity even if the next native
        // window is already populated by the time we read it.
        overlay.clear();
      }
      const started = performance.now(); latestView = view; renderCurrentView();
      if (view.runtimeMode && (view.state === "ready" || view.state === "no-pending")) recoverPreferences();
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
    if (detached) return;
    if (reason === "navigation" || reason === "disabled") vocabulary.reset();
    if (reason) {
      if (reason !== "interrupted") record({ outcome: reason });
      interruptPresented(); debugSent.clear(); debugStarted.clear();
    }
    renderCurrentView(); updateDiagnostics();
  });
  capture = new CaptionCapture(lifecycle, coordinator, {
    sha256, now: () => performance.now(),
    onWaiting: () => { latestView = { state: "waiting" }; renderCurrentView(); },
    onSource: source => { if (source) renderCurrentView(); else overlay.bridgeSourceGap(); },
    onObserved: sequence => { observedAt = performance.now(); observedSequence = sequence; record({ outcome: "observed" }); },
    onOversized: () => record({ outcome: "oversized" }),
    onAcquisition: elapsedMs => record({ stage: "acquisition", elapsedMs }),
    onObservation: outcome => record({ outcome }),
    onInterrupted: interruptPresented
  });

  renderCurrentView();
  let lineLimitRevision = 0;
  function updateLineLimit(value: unknown): void {
    if (!detached && overlay.setLineLimit(captionLineLimit(value))) renderCurrentView();
  }
  const storage = chrome.storage;
  storage?.onChanged?.addListener((changes, area) => {
    if (area === "local" && changes[CAPTION_LINE_LIMIT_KEY]) {
      lineLimitRevision++;
      updateLineLimit(changes[CAPTION_LINE_LIMIT_KEY].newValue);
    }
  });
  void storage?.local?.get(CAPTION_LINE_LIMIT_KEY).then(data => {
    if (lineLimitRevision === 0) updateLineLimit(data[CAPTION_LINE_LIMIT_KEY]);
  }).catch(() => undefined);
  void updatePreferences("read");
  chrome.runtime.onMessage?.addListener((message, sender, respond) => {
    if (detached || sender.id !== extensionId || !["page-enhancement", "page-vocabulary"].includes(message?.type)) return;
    if (message.type === "page-vocabulary" && !isManagementUrl(sender.url, chrome.runtime.getURL("manage.html"))) return;
    if (lifecycle.videoId !== videoIdFromLocation()) lifecycle.refreshPage();
    if (!videoIdFromLocation()) { respond({ ok: false, reason: "not-video-page" }); return; }
    if (lifecycle.navigating || !document.querySelector(".html5-video-player, #movie_player")) {
      respond({ ok: false }); return;
    }
    if (message.type === "page-vocabulary") {
      if (message.pageKey !== lifecycle.pageKey) { respond({ ok: false }); return; }
      respond({ ok: true, pageKey: lifecycle.pageKey, rows: vocabulary.snapshot() }); return;
    }
    if (message.action === "set") {
      if (!lifecycle.setEnabled(message.pageKey, message.enabled)) { respond({ ok: false }); return; }
      captureLivePage();
    } else if (message.action !== "read") { respond({ ok: false }); return; }
    const captionStatus = displayedHints > 0 && capture?.currentCaption() ? "shown"
      : !capture?.currentCaption() ? "no-caption"
      : latestView.state === "fallback" ? "unavailable"
      : !preferenceReady ? "preferences-unavailable"
      : latestView.state === "no-pending" || latestView.state === "ready" ? "no-hint" : "waiting";
    respond({ ok: true, enabled: lifecycle.enabled, pageKey: lifecycle.pageKey, captionStatus });
  });
  window.addEventListener("pagehide", () => vocabulary.reset());
  chrome.runtime.onMessage?.addListener((message, sender) => {
    if (detached || sender.id !== extensionId) return;
    if (message?.type === "preferences-changed") void updatePreferences("read");
    if (message?.type === "backend-connection-changed") {
      const revision = ++connectionRevision;
      preferenceRetryAt = 0;
      preferenceRecovery = undefined;
      vocabulary.reset();
      connectionSwitching = true; preferenceReady = false; suppressed.clear(); displayedHints = 0;
      preferenceMessage = "正在切换本机服务，英文不受影响。";
      capture?.invalidate(); debugSent.clear(); debugStarted.clear();
      void updatePreferences("read").finally(() => {
        if (detached || revision !== connectionRevision) return;
        connectionSwitching = false;
        captureLivePage();
      });
    }
  });
  window.addEventListener("message", event => {
    if (!detached && event.source === window && event.origin === location.origin && event.data?.type === "lexiflow-native-captions") {
      capture?.acceptNativeTrack(event.data.track, videoIdFromLocation()); captureLivePage();
    }
  }, { signal: listeners.signal });
  window.postMessage({ type: "lexiflow-native-ready" }, location.origin);
  lifecycle.attach();
}
