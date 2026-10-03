import { LocalPreferences, PREFERENCE_KEY, type PreferenceAction, type PreferenceResult } from "./preferences";
import {
  API_URL,
  REQUEST_TIMEOUT_MS,
  isCaptionHintRequest,
  parseHintResponse,
  isStableId,
  type ApiResult,
  type ApiTimings,
  type CaptionHintRequest
} from "./protocol";
import { readRuntimeStatus } from "./runtime-status";

type HintMessage = { type: "caption-hints"; requestId: string; payload: CaptionHintRequest };
type CancelMessage = { type: "cancel-caption-hint"; requestId: string };
type RuntimeStatusMessage = { type: "runtime-status" };
type CaptionDebugMessage = { type: "caption-debug"; payload: DebugEvent };

const preferences = new LocalPreferences({
  read: async () => (await chrome.storage.local.get(PREFERENCE_KEY))[PREFERENCE_KEY],
  write: async ids => { await chrome.storage.local.set({ [PREFERENCE_KEY]: ids }); }
});
const inFlight = new Map<string, AbortController>();
const DEBUG_URL = API_URL.replace(/caption-hints$/, "caption-debug");
type DebugEvent = { eventId: string; event: "video-start" | "incremental" | "final" | "interrupted";
  topicKey: string; videoId: string; subtitleKey: string; positionMs: number; text: string };
const debugQueue: Array<{ event: DebugEvent; bytes: number }> = [];
const DEBUG_MAX_EVENTS = 32, DEBUG_MAX_BYTES = 32 * 1024;
let debugBytes = 0, debugDropped = 0, debugDraining = false;
const debugId = (): string => crypto.randomUUID();
function trustedVideo(sender: chrome.runtime.MessageSender): string | null {
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab?.id || typeof sender.tab.url !== "string") return null;
  try {
    const url = new URL(sender.tab.url);
    if (url.protocol !== "https:" || url.hostname !== "www.youtube.com" || url.pathname !== "/watch") return null;
    const id = url.searchParams.get("v");
    return id && /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null;
  } catch { return null; }
}
function validDebugEvent(value: unknown): value is DebugEvent {
  if (!value || typeof value !== "object") return false;
  const item = value as DebugEvent;
  return isStableId(item.eventId) && ["video-start", "incremental", "final", "interrupted"].includes(item.event) &&
    typeof item.topicKey === "string" && item.topicKey.length > 0 && item.topicKey.length <= 128 &&
    typeof item.videoId === "string" && /^[A-Za-z0-9_-]{11}$/.test(item.videoId) &&
    typeof item.subtitleKey === "string" && item.subtitleKey.length > 0 && item.subtitleKey.length <= 128 &&
    Number.isSafeInteger(item.positionMs) && item.positionMs >= 0 &&
    typeof item.text === "string" && item.text.length <= 16384;
}
async function withDeadline<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController(); let timeout: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => { controller.abort(); reject(new Error("debug-timeout")); }, REQUEST_TIMEOUT_MS);
  });
  try { return await Promise.race([operation(controller.signal), deadline]); }
  finally { if (timeout !== undefined) clearTimeout(timeout); }
}
async function boundedFetch(url: string, init: RequestInit): Promise<Response> {
  return withDeadline(signal => fetch(url, { ...init, signal }));
}
async function readDebugCapability(): Promise<boolean> {
  try { return await withDeadline(async signal => {
    const response = await fetch(DEBUG_URL, { method: "GET", cache: "no-store", redirect: "error", credentials: "omit",
      referrerPolicy: "no-referrer", signal });
    if (!response.ok || response.headers?.get("Cache-Control")?.toLowerCase().split(",").some(value => value.trim() === "no-store") !== true) return false;
    const length = Number(response.headers?.get("Content-Length"));
    if (Number.isFinite(length) && length > 256) return false;
    let text = "";
    if (response.body?.getReader) {
      const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let total = 0;
      try {
        while (true) {
          const part = await reader.read(); if (part.done) break;
          total += part.value.byteLength; if (total > 256) { await reader.cancel(); return false; }
          chunks.push(part.value);
        }
      } finally { reader.releaseLock(); }
      text = new TextDecoder().decode(chunks.length === 1 ? chunks[0] : concatenate(chunks, total));
    } else {
      const value = await response.text(); if (new TextEncoder().encode(value).byteLength > 256) return false; text = value;
    }
    const value: unknown = JSON.parse(text);
    return !!value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 1 &&
      (value as { enabled?: unknown }).enabled === true;
  }); } catch { return false; }
}
function concatenate(chunks: Uint8Array[], total: number): Uint8Array {
  const output = new Uint8Array(total); let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.byteLength; }
  return output;
}
async function sendDebug(event: DebugEvent): Promise<void> {
  try {
    if (!await readDebugCapability()) return;
    const response = await boundedFetch(DEBUG_URL, { method: "POST", cache: "no-store", redirect: "error", credentials: "omit", referrerPolicy: "no-referrer",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(event) });
    if (!response.ok) return;
  } catch { /* private diagnostics are best effort and never affect captions */ }
}
function enqueueDebug(value: unknown): void {
  if (!validDebugEvent(value)) return;
  const event = value as DebugEvent; const bytes = new TextEncoder().encode(JSON.stringify(event)).byteLength;
  if (debugQueue.length >= DEBUG_MAX_EVENTS || debugBytes + bytes > DEBUG_MAX_BYTES) { debugDropped++; return; }
  debugQueue.push({ event, bytes }); debugBytes += bytes;
  if (!debugDraining) void drainDebug();
}
async function drainDebug(): Promise<void> {
  debugDraining = true;
  try {
    while (debugQueue.length) {
      const item = debugQueue[0];
      await sendDebug(item.event);
      debugQueue.shift(); debugBytes -= item.bytes;
      if (debugDropped && debugQueue.length < DEBUG_MAX_EVENTS) {
        const dropped = debugDropped; debugDropped = 0;
        const summary: DebugEvent = { eventId: debugId(), event: "interrupted", topicKey: item.event.topicKey,
          videoId: item.event.videoId, subtitleKey: item.event.subtitleKey, positionMs: item.event.positionMs,
          text: `调试事件队列已丢弃 ${dropped} 条；日志不保证完整。` };
        await sendDebug(summary);
      }
    }
  } finally { debugDraining = false; if (debugQueue.length) void drainDebug(); }
}

/** Only fixed timing dimensions are accepted; header descriptions never reach diagnostics. */
function parseServerTiming(header: string | null): { timings?: ApiTimings } {
  if (!header || header.length > 512) return {};
  const timings: ApiTimings = {};
  const seen = new Set<string>(), invalid = new Set<string>();
  for (const part of header.split(",")) {
    const dimension = /^(query|rules|api)(?:\s*;|$)/.exec(part.trim())?.[1];
    if (dimension && seen.has(dimension)) { invalid.add(dimension); delete timings[dimension as keyof ApiTimings]; continue; }
    if (dimension) seen.add(dimension);
    const match = /^(query|rules|api);dur=(\d+(?:\.\d+)?)$/.exec(part.trim());
    if (!match) continue;
    const duration = Number(match[2]);
    if (Number.isFinite(duration) && duration <= 60_000) timings[match[1] as keyof ApiTimings] = duration;
  }
  for (const dimension of invalid) delete timings[dimension as keyof ApiTimings];
  return Object.keys(timings).length ? { timings } : {};
}

async function requestHints(key: string, payload: CaptionHintRequest): Promise<ApiResult> {
  if (!isCaptionHintRequest(payload)) {
    return { ok: false, reason: "invalid-request" };
  }
  const started = performance.now();
  let outcome = "network";
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
  inFlight.get(key)?.abort();
  inFlight.set(key, controller);
  try {
    const gate = await readRuntimeStatus(controller.signal);
    if (controller.signal.aborted) throw new Error("aborted");
    if (!gate.ok || (gate.status.mode !== "demo" && !gate.status.ready)) {
      outcome = gate.ok ? "backend_unavailable" : gate.reason;
      return { ok: false, reason: gate.ok ? "backend_unavailable" : gate.reason === "timeout" ? "timeout" : gate.reason === "network" ? "network" : "invalid-response" };
    }
    const response = await fetch(API_URL, {
      method: "POST",
      // 本机地址也不能通过重定向转发字幕或继承浏览器凭据。
      redirect: "error",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    if (!response.ok) {
      outcome = response.status === 503 ? "backend_unavailable" : "rejected";
      return { ok: false, reason: outcome === "backend_unavailable" ? "backend_unavailable" : "rejected" };
    }
    let data: unknown;
    try { data = await response.json(); }
    catch (error) {
      if (controller.signal.aborted) throw error;
      outcome = "invalid-response";
      return { ok: false, reason: "invalid-response" };
    }
    const body = parseHintResponse(data, payload);
    outcome = body ? (body.hints.length ? "READY" : "NO_PENDING") : "invalid-response";
    const timings = parseServerTiming(response.headers?.get("Server-Timing") ?? null);
    const rawRequestId = response.headers?.get("X-Request-ID");
    const requestId = isStableId(rawRequestId) ? rawRequestId : undefined;
    return body === undefined ? { ok: false, reason: "invalid-response" } : { ok: true, body, ...(requestId ? { requestId } : {}), ...timings };
  } catch {
    const reason = timedOut ? "timeout" : controller.signal.aborted ? "aborted" : "network";
    outcome = reason;
    return { ok: false, reason };
  } finally {
    try { console.info("[LexiFlow]", { stage: "api", outcome, elapsedMs: Math.round(performance.now() - started) }); } catch { /* diagnostics cannot affect request cleanup */ }
    clearTimeout(timeout);
    if (inFlight.get(key) === controller) inFlight.delete(key);
  }
}

chrome.runtime.onMessage.addListener(
  (request: HintMessage | CancelMessage | RuntimeStatusMessage | CaptionDebugMessage | { type: "local-preferences"; action: PreferenceAction; entryId?: string; lexiconVersion?: number }, sender,
    sendResponse: (response: ApiResult | PreferenceResult | { ok: true } | { ok: true; status: unknown } | { ok: false; reason: string }) => void) => {
    if (request?.type === "caption-debug") {
      const videoId = trustedVideo(sender);
      if (!videoId || !validDebugEvent(request.payload) || request.payload.videoId !== videoId) {
        sendResponse({ ok: false, reason: "invalid-request" }); return undefined;
      }
      enqueueDebug(request.payload); sendResponse({ ok: true }); return undefined;
    }
    if (request?.type === "runtime-status") {
      const popup = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("popup.html") && sender.tab === undefined;
      if (!popup) { sendResponse({ ok: false, reason: "invalid-request" }); return undefined; }
      void readRuntimeStatus().then(sendResponse);
      return true;
    }
    if (request?.type === "local-preferences") {
      const popupUrl = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("popup.html");
      const popup = popupUrl && sender.tab === undefined;
      const popupAction = request.action === "read" || request.action === "restore-all";
      if (sender.id !== chrome.runtime.id || (popupUrl ? !popup || !popupAction : sender.tab?.id === undefined)) {
        sendResponse({ ok: false, reason: "invalid-request" }); return undefined;
      }
      void preferences.execute(request.action, request.entryId, request.lexiconVersion).then(sendResponse);
      return true;
    }
    if (request?.type !== "caption-hints" && request?.type !== "cancel-caption-hint") return undefined;
    if (typeof request.requestId !== "string" || request.requestId.length === 0 || request.requestId.length > 128 || sender.tab?.id === undefined) {
      sendResponse({ ok: false, reason: "invalid-request" });
      return undefined;
    }
    const key = `${sender.tab.id}:${sender.documentId ?? sender.frameId ?? 0}:${request.requestId}`;
    if (request?.type === "cancel-caption-hint") {
      inFlight.get(key)?.abort();
      sendResponse({ ok: true });
      return undefined;
    }
    if (request?.type !== "caption-hints") return undefined;
    void requestHints(key, request.payload).then(sendResponse);
    return true;
  }
);
