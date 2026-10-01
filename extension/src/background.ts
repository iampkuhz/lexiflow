import { LocalPreferences, PREFERENCE_KEY, type PreferenceAction, type PreferenceResult } from "./preferences";
import {
  API_URL,
  REQUEST_TIMEOUT_MS,
  isCaptionHintRequest,
  parseHintResponse,
  type ApiResult,
  type ApiTimings,
  type CaptionHintRequest
} from "./protocol";
import { readRuntimeStatus } from "./runtime-status";

type HintMessage = { type: "caption-hints"; requestId: string; payload: CaptionHintRequest };
type CancelMessage = { type: "cancel-caption-hint"; requestId: string };
type RuntimeStatusMessage = { type: "runtime-status" };

const preferences = new LocalPreferences({
  read: async () => (await chrome.storage.local.get(PREFERENCE_KEY))[PREFERENCE_KEY],
  write: async ids => { await chrome.storage.local.set({ [PREFERENCE_KEY]: ids }); }
});
const inFlight = new Map<string, AbortController>();

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
    return body === undefined ? { ok: false, reason: "invalid-response" } : { ok: true, body, ...timings };
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
  (request: HintMessage | CancelMessage | RuntimeStatusMessage | { type: "local-preferences"; action: PreferenceAction; entryId?: string; lexiconVersion?: number }, sender,
    sendResponse: (response: ApiResult | PreferenceResult | { ok: true } | { ok: true; status: unknown } | { ok: false; reason: string }) => void) => {
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
