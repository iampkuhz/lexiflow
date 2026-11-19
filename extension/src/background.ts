import { isCutoff, isHiddenPreference, parsePreferences, type PreferenceAction, type PreferenceResult, type RecentItem } from "./preferences";
import {
  API_URL,
  REQUEST_TIMEOUT_MS,
  MAX_PRESENTED_ENTRY_IDS,
  isEntryId,
  isCaptionHintRequest,
  parseHintResponse,
  isStableId,
  type ApiResult,
  type ApiTimings,
  type CaptionHintRequest
} from "./protocol";
import { readRuntimeStatus, runtimeCompatibility } from "./runtime-status";

type HintMessage = { type: "caption-hints"; requestId: string; payload: CaptionHintRequest };
type CancelMessage = { type: "cancel-caption-hint"; requestId: string };
type RuntimeStatusMessage = { type: "runtime-status" };
type CaptionDebugMessage = { type: "caption-debug"; payload: DebugEvent };

// Explicit preferences are facts owned by the localhost preferences service, never browser storage.
const PREFERENCES_URL = API_URL.replace(/caption-hints$/, "preferences");
let cachedPreferences: ReturnType<typeof parsePreferences>;
let preferencesLoadedAt = 0;
let pendingPreferenceRead: Promise<unknown> | undefined;
let preferenceMutationQueue: Promise<unknown> = Promise.resolve();
let queuedPreferenceMutations = 0;
const PREFERENCE_CACHE_TTL_MS = 10_000;
type PreferenceOperation = { action: "read" } | { action: "setCutoff"; commonWordCutoff: number } |
  { action: "hide"; entryId: string; lemma: string; gloss: string } | { action: "restore"; entryId: string };
async function preferenceRequest(operation: PreferenceOperation): Promise<unknown> {
  try {
    const response = await boundedFetch(PREFERENCES_URL, { method: "POST", cache: "no-store", redirect: "error",
      credentials: "omit", referrerPolicy: "no-referrer", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(operation) });
    if (!response.ok) return { ok: false, reason: response.status >= 500 ? "backend_unavailable" : "rejected" };
    const state = parsePreferences(await response.json());
    return state ? { ok: true, ...state } : { ok: false, reason: "invalid-response" };
  } catch { return { ok: false, reason: "network" }; }
}
async function readPreferenceSnapshot(force = false): Promise<unknown> {
  if (force || queuedPreferenceMutations > 0) await preferenceMutationQueue;
  if (!force && cachedPreferences && Date.now() - preferencesLoadedAt < PREFERENCE_CACHE_TTL_MS)
    return { ok: true, ...cachedPreferences };
  if (pendingPreferenceRead) return pendingPreferenceRead;
  pendingPreferenceRead = preferenceRequest({ action: "read" }).then(result => {
    if ((result as { ok?: boolean }).ok) {
      cachedPreferences = parsePreferences(result);
      preferencesLoadedAt = Date.now();
    }
    return result;
  }).finally(() => { pendingPreferenceRead = undefined; });
  return pendingPreferenceRead;
}
type LexiconItem = { entryId: string; lemma: string; gloss: string | null; frequencyRank: number | null; hintState: "eligible" | "blocked" };
function parseLexicon(value: unknown): { items: LexiconItem[]; total: number; page: number; pageSize: number } | undefined {
  if (!value || typeof value !== "object") return;
  const page = value as { items?: unknown; total?: unknown; page?: unknown; pageSize?: unknown };
  if (!Array.isArray(page.items) || !Number.isSafeInteger(page.total) || (page.total as number) < 0 ||
      !Number.isSafeInteger(page.page) || (page.page as number) < 1 || !Number.isSafeInteger(page.pageSize) || (page.pageSize as number) < 1) return;
  const items: LexiconItem[] = [];
  for (const raw of page.items) {
    if (!raw || typeof raw !== "object") return;
    const item = raw as LexiconItem;
    if (!isEntryId(item.entryId) || typeof item.lemma !== "string" || !item.lemma.length || item.lemma.length > 128 ||
        (item.gloss !== null && (typeof item.gloss !== "string" || !item.gloss.length || item.gloss.length > 24)) ||
        (item.frequencyRank !== null && (!Number.isSafeInteger(item.frequencyRank) || item.frequencyRank < 1)) ||
        !["eligible", "blocked"].includes(item.hintState)) return;
    items.push(item);
  }
  return { items, total: page.total as number, page: page.page as number, pageSize: page.pageSize as number };
}
const RECENT_URL = API_URL.replace(/caption-hints$/, "recent-hints");
type RecentOperation = { action: "read" } | { action: "record"; entryIds: string[] };
function parseRecent(value: unknown): RecentItem[] | undefined {
  if (!value || typeof value !== "object" || !("items" in value) || !Array.isArray(value.items)) return;
  const items: RecentItem[] = [], identities = new Set<string>();
  for (const item of value.items) {
    if (!isHiddenPreference(item) || !("count" in item) || typeof item.count !== "number" ||
        !Number.isSafeInteger(item.count) || item.count < 1 || identities.has(item.entryId)) return;
    const { entryId, lemma, gloss, count } = item;
    identities.add(entryId);
    items.push({ entryId, lemma, gloss, count });
  }
  return items;
}
async function recentRequest(operation: RecentOperation): Promise<unknown> {
  try {
    return await withDeadline(async signal => {
      const response = await fetch(RECENT_URL, { method: "POST", cache: "no-store", redirect: "error",
        credentials: "omit", referrerPolicy: "no-referrer", headers: { "Content-Type": "application/json" },
        signal, body: JSON.stringify(operation) });
      if (!response.ok) return { ok: false, reason: "backend_unavailable" };
      if (operation.action === "record")
        return response.status === 204 ? { ok: true } : { ok: false, reason: "invalid-response" };
      const items = parseRecent(await response.json());
      return items ? { ok: true, items } : { ok: false, reason: "invalid-response" };
    });
  } catch { return { ok: false, reason: "network" }; }
}
async function broadcastPreferenceChange(): Promise<void> {
  try {
    const tabs = await chrome.tabs.query({});
    // No tabs permission: Chrome may omit URLs here. Only our installed content scripts receive this signal.
    await Promise.all(tabs.flatMap(tab => typeof tab.id === "number"
      ? [chrome.tabs.sendMessage(tab.id, { type: "preferences-changed" }, { frameId: 0 }).catch(() => undefined)] : []));
  } catch { /* a preference write is already confirmed; refresh delivery is best effort */ }
}
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
    const expectedVersion = chrome.runtime.getManifest().version_name;
    if (!gate.ok) {
      outcome = gate.reason;
      return { ok: false, reason: gate.reason === "timeout" ? "timeout" : gate.reason === "network" ? "network" : "invalid-response" };
    }
    const compatibility = runtimeCompatibility(gate.status, expectedVersion);
    if (gate.status.reason === "INITIALIZING") {
      outcome = "runtime-initializing";
      return { ok: false, reason: "runtime-initializing" };
    }
    if (compatibility || gate.status.reason === "SCHEMA_MISMATCH" || gate.status.reason === "IDENTITY_MISMATCH") {
      outcome = "runtime-incompatible";
      return { ok: false, reason: "runtime-incompatible" };
    }
    if (gate.status.mode !== "demo" && !gate.status.ready) {
      outcome = "backend_unavailable";
      return { ok: false, reason: "backend_unavailable" };
    }
    const storedPreferences = await readPreferenceSnapshot();
    const reason = storedPreferences && typeof storedPreferences === "object" ? (storedPreferences as { reason?: string }).reason : undefined;
    const fetchedPreferences = storedPreferences && typeof storedPreferences === "object" && (storedPreferences as { ok?: boolean }).ok
      ? parsePreferences(storedPreferences) : undefined;
    const unavailable = reason === "network" || reason === "backend_unavailable";
    const preferences = fetchedPreferences ?? (unavailable ? cachedPreferences : undefined);
    if (!preferences && !unavailable) return { ok: false, reason: "invalid-response" };
    const requestPayload: CaptionHintRequest = { ...payload, commonWordCutoff: preferences?.commonWordCutoff ?? 1500 };
    const response = await fetch(API_URL, {
      method: "POST",
      // 本机地址也不能通过重定向转发字幕或继承浏览器凭据。
      redirect: "error",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
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
    const body = parseHintResponse(data, requestPayload);
    outcome = body ? (body.hints.length ? "READY" : "NO_PENDING") : "invalid-response";
    const timings = parseServerTiming(response.headers?.get("Server-Timing") ?? null);
    const rawRequestId = response.headers?.get("X-Request-ID");
    const requestId = isStableId(rawRequestId) ? rawRequestId : undefined;
    return body === undefined ? { ok: false, reason: "invalid-response" } : { ok: true, body, runtimeMode: gate.status.mode === "demo" ? "demo" : "formal", ...(requestId ? { requestId } : {}), ...timings };
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
  (request: HintMessage | CancelMessage | RuntimeStatusMessage | CaptionDebugMessage | { type: "local-preferences"; action: PreferenceAction; entryId?: string; lemma?: string; gloss?: string; commonWordCutoff?: number } |
    { type: "local-recent" } | { type: "local-lexicon"; query: string; sort: "frequency" | "alphabet"; page: number; pageSize: number } |
    { type: "local-presented"; entryIds: string[] }, sender,
    sendResponse: (response: any) => void) => {
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
      void readRuntimeStatus().then(result => {
        if (!result.ok) { sendResponse(result); return; }
        const compatibility = runtimeCompatibility(result.status, chrome.runtime.getManifest().version_name);
        sendResponse(compatibility ? { ok: false, reason: compatibility } : result);
      });
      return true;
    }
    const popup = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("popup.html") && sender.tab === undefined;
    const manage = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("manage.html");
    const video = !!trustedVideo(sender);
    if (request?.type === "local-recent") {
      if (!manage || Object.keys(request).length !== 1) { sendResponse({ ok: false, reason: "invalid-request" }); return undefined; }
      void recentRequest({ action: "read" }).then(sendResponse);
      return true;
    }
    if (request?.type === "local-lexicon") {
      if (!manage || typeof request.query !== "string" || request.query.length > 128 || !["frequency", "alphabet"].includes(request.sort) ||
          !Number.isSafeInteger(request.page) || request.page < 1 || !Number.isSafeInteger(request.pageSize) || request.pageSize < 1 || request.pageSize > 100) {
        sendResponse({ ok: false, reason: "invalid-request" }); return undefined;
      }
      const url = API_URL.replace(/caption-hints$/, "lexicon");
      const params = new URLSearchParams({ query: request.query, sort: request.sort, page: String(request.page), pageSize: String(request.pageSize) });
      void boundedFetch(`${url}?${params}`, { method: "GET", cache: "no-store", redirect: "error", credentials: "omit", referrerPolicy: "no-referrer" })
        .then(async response => response.ok ? parseLexicon(await response.json()) : undefined)
        .then(body => sendResponse(body ? { ok: true, ...body } : { ok: false, reason: "unavailable" }))
        .catch(() => sendResponse({ ok: false, reason: "network" }));
      return true;
    }
    if (request?.type === "local-presented") {
      if (!video || Object.keys(request).length !== 2 || !Array.isArray(request.entryIds) ||
          request.entryIds.length < 1 || request.entryIds.length > MAX_PRESENTED_ENTRY_IDS || !request.entryIds.every(isEntryId)) {
        sendResponse({ ok: false, reason: "invalid-request" }); return undefined;
      }
      void recentRequest({ action: "record", entryIds: request.entryIds }).then(sendResponse);
      return true;
    }
    if (request?.type === "local-preferences") {
      const permitted = (request.action === "read" && (popup || manage || video)) ||
        (request.action === "setCutoff" && manage) ||
        ((request.action === "hide" || request.action === "restore") && (manage || video));
      if (!permitted) {
        sendResponse({ ok: false, reason: "invalid-request" }); return undefined;
      }
      const operation = request.action === "read" ? { action: "read" } as const
        : request.action === "setCutoff" && isCutoff(request.commonWordCutoff)
          ? { action: "setCutoff", commonWordCutoff: request.commonWordCutoff! } as const
        : request.action === "hide" && typeof request.entryId === "string" && typeof request.lemma === "string" && typeof request.gloss === "string"
          ? { action: "hide", entryId: request.entryId, lemma: request.lemma, gloss: request.gloss } as const
          : request.action === "restore" && typeof request.entryId === "string" ? { action: "restore", entryId: request.entryId } as const : undefined;
      if (!operation) { sendResponse({ ok: false, reason: "invalid-request" }); return undefined; }
      let operationPromise: Promise<unknown>;
      if (operation.action === "read") operationPromise = readPreferenceSnapshot(true);
      else {
        const readBeforeMutation = pendingPreferenceRead ?? Promise.resolve();
        queuedPreferenceMutations++;
        operationPromise = preferenceMutationQueue.then(() => readBeforeMutation).then(() => preferenceRequest(operation)).then(value => {
          const updated = (value as { ok?: boolean }).ok ? parsePreferences(value) : undefined;
          if (updated) { cachedPreferences = updated; preferencesLoadedAt = Date.now(); }
          else cachedPreferences = undefined;
          return value;
        }).finally(() => { queuedPreferenceMutations = Math.max(0, queuedPreferenceMutations - 1); });
        preferenceMutationQueue = operationPromise.then(() => undefined, () => undefined);
      }
      void operationPromise.then(value => {
        if ((request.action === "hide" || request.action === "restore" || request.action === "setCutoff") && (value as { ok?: boolean }).ok)
          void broadcastPreferenceChange();
        if (request.action === "read" && popup) {
          const state = value as { ok?: boolean; reason?: string; commonWordCutoff?: number; hidden?: unknown[] };
          sendResponse(state.ok && Number.isInteger(state.commonWordCutoff) && Array.isArray(state.hidden)
            ? { ok: true, commonWordCutoff: state.commonWordCutoff, hiddenCount: state.hidden.length }
            : { ok: false, reason: state.reason ?? "invalid-response" });
        } else sendResponse(value as { ok: false; reason: string });
      });
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
