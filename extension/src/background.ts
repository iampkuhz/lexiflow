import { isManagementUrl } from "./manage-context";
import { isCutoff, isHiddenPreference, parsePreferences, type PreferenceAction, type RecentItem } from "./preferences";
import {
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
import { readRuntimeStatus, type StatusRead } from "./runtime-status";
import { CONNECTION_KEY, DEFAULT_ORIGIN, endpoint, loopbackOrigin, parseConnection, type Connection } from "./backend-target";

type ConnectionMessage = { type: "backend-connection"; origin: unknown };
type Route = { origin: string };
let configuration: Promise<Connection | undefined> | undefined;
let routeRevision = 0;

function currentConfiguration(): Promise<Connection | undefined> {
  return configuration ??= (async () => {
    try { return parseConnection((await chrome.storage?.local?.get(CONNECTION_KEY))?.[CONNECTION_KEY]); }
    catch { return undefined; }
  })();
}
const usable = (result: StatusRead): boolean => result.ok &&
  (result.status.ready || result.status.mode === "demo");

async function activeRoute(): Promise<Route> {
  const revision = routeRevision;
  const selected = await currentConfiguration();
  if (revision !== routeRevision) throw new Error("connection changed");
  return { origin: selected?.origin ?? DEFAULT_ORIGIN };
}
// 任何本机数据操作都先验证服务。端口可被别的本机程序占用。
async function localDataRoute(): Promise<Route> {
  const revision = routeRevision;
  const route = await activeRoute();
  const result = await readRuntimeStatus(undefined, route.origin);
  if (!usable(result)) throw new Error("runtime unavailable");
  if (revision !== routeRevision) throw new Error("connection changed");
  return route;
}

type HintMessage = { type: "caption-hints"; requestId: string; payload: CaptionHintRequest };
type CancelMessage = { type: "cancel-caption-hint"; requestId: string };
type RuntimeStatusMessage = { type: "runtime-status" };
type CaptionDebugMessage = { type: "caption-debug"; payload: DebugEvent };

// Explicit preferences are facts owned by the localhost preferences service, never browser storage.
let cachedPreferences: { origin: string; revision: number; value: NonNullable<ReturnType<typeof parsePreferences>>; loadedAt: number } | undefined;
const pendingPreferenceReads = new Map<string, Promise<unknown>>();
let preferenceMutationQueue: Promise<unknown> = Promise.resolve();
let queuedPreferenceMutations = 0;
const PREFERENCE_CACHE_TTL_MS = 10_000;
type PreferenceOperation = { action: "read" } | { action: "setCutoff"; commonWordCutoff: number } |
  { action: "hide"; entryId: string } | { action: "restore"; entryId: string };
async function preferenceRequest(operation: PreferenceOperation, origin: string): Promise<unknown> {
  try {
    const response = await boundedFetch(endpoint(origin, "preferences"), { method: "POST", cache: "no-store", redirect: "error",
      credentials: "omit", referrerPolicy: "no-referrer", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(operation) });
    if (!response.ok) return { ok: false, reason: response.status >= 500 ? "backend_unavailable" : "rejected" };
    const state = parsePreferences(await response.json());
    return state ? { ok: true, ...state } : { ok: false, reason: "invalid-response" };
  } catch { return { ok: false, reason: "network" }; }
}
async function readPreferenceSnapshot(origin: string, force = false): Promise<unknown> {
  const revision = routeRevision, key = `${revision}:${origin}`;
  if (force || queuedPreferenceMutations > 0) await preferenceMutationQueue;
  if (revision !== routeRevision) return { ok: false, reason: "network" };
  const cached = preferencesFor(origin);
  if (!force && cached && Date.now() - cached.loadedAt < PREFERENCE_CACHE_TTL_MS)
    return { ok: true, ...cached.value };
  const existing = pendingPreferenceReads.get(key);
  if (existing) return existing;
  const read = preferenceRequest({ action: "read" }, origin).then(result => {
    cachePreferences(origin, revision, result);
    return result;
  }).finally(() => { pendingPreferenceReads.delete(key); });
  pendingPreferenceReads.set(key, read);
  return read;
}
function preferencesFor(origin: string) {
  return cachedPreferences?.origin === origin && cachedPreferences.revision === routeRevision ? cachedPreferences : undefined;
}
function cachePreferences(origin: string, revision: number, result: unknown) {
  if (revision !== routeRevision) return;
  const value = (result as { ok?: boolean })?.ok ? parsePreferences(result) : undefined;
  if (value) cachedPreferences = { origin, revision, value, loadedAt: Date.now() };
}
type LexiconItem = { entryId: string; lemma: string; gloss: string | null; frequencyRank: number | null; hintState: "eligible" | "blocked" | "hidden" };
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
        !["eligible", "blocked", "hidden"].includes(item.hintState)) return;
    items.push(item);
  }
  return { items, total: page.total as number, page: page.page as number, pageSize: page.pageSize as number };
}
type RecentOperation = { action: "read" } | { action: "record"; entryIds: string[] };
function parseRecent(value: unknown): RecentItem[] | undefined {
  if (!value || typeof value !== "object" || !("items" in value) || !Array.isArray(value.items)) return;
  const items: RecentItem[] = [], identities = new Set<string>();
  for (const item of value.items) {
    if (!isHiddenPreference(item) || !("count" in item) || typeof item.count !== "number" ||
        !Number.isSafeInteger(item.count) || item.count < 1 || identities.has(item.entryId)) return;
    const { entryId, lemma, gloss, count, frequencyRank } = item;
    identities.add(entryId);
    items.push({ entryId, lemma, gloss, count, frequencyRank });
  }
  return items;
}
async function recentRequest(operation: RecentOperation): Promise<unknown> {
  try {
    const { origin } = await localDataRoute();
    return await withDeadline(async signal => {
      const response = await fetch(endpoint(origin, "recent-hints"), { method: "POST", cache: "no-store", redirect: "error",
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
async function broadcastPreferenceChange(type = "preferences-changed"): Promise<void> {
  try {
    const tabs = await chrome.tabs.query({});
    // No tabs permission: Chrome may omit URLs here. Only our installed content scripts receive this signal.
    await Promise.all(tabs.flatMap(tab => typeof tab.id === "number"
      ? [chrome.tabs.sendMessage(tab.id, { type }, { frameId: 0 }).catch(() => undefined)] : []));
  } catch { /* a preference write is already confirmed; refresh delivery is best effort */ }
}
const inFlight = new Map<string, AbortController>();
type DebugEvent = { eventId: string; event: "video-start" | "incremental" | "final" | "interrupted";
  topicKey: string; videoId: string; subtitleKey: string; positionMs: number; text: string };
const debugQueue: Array<{ event: DebugEvent; bytes: number; revision: number }> = [];
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
async function readDebugCapability(origin: string): Promise<boolean> {
  try { return await withDeadline(async signal => {
    const response = await fetch(endpoint(origin, "caption-debug"), { method: "GET", cache: "no-store", redirect: "error", credentials: "omit",
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
async function sendDebug(event: DebugEvent, revision: number): Promise<void> {
  try {
    if (revision !== routeRevision) return;
    const { origin } = await localDataRoute();
    if (!await readDebugCapability(origin) || revision !== routeRevision) return;
    const response = await boundedFetch(endpoint(origin, "caption-debug"), { method: "POST", cache: "no-store", redirect: "error", credentials: "omit", referrerPolicy: "no-referrer",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(event) });
    if (!response.ok) return;
  } catch { /* private diagnostics are best effort and never affect captions */ }
}
function enqueueDebug(value: unknown): void {
  if (!validDebugEvent(value)) return;
  const event = value as DebugEvent; const bytes = new TextEncoder().encode(JSON.stringify(event)).byteLength;
  if (debugQueue.length >= DEBUG_MAX_EVENTS || debugBytes + bytes > DEBUG_MAX_BYTES) { debugDropped++; return; }
  debugQueue.push({ event, bytes, revision: routeRevision }); debugBytes += bytes;
  if (!debugDraining) void drainDebug();
}
async function drainDebug(): Promise<void> {
  debugDraining = true;
  try {
    while (debugQueue.length) {
      const item = debugQueue[0];
      await sendDebug(item.event, item.revision);
      debugQueue.shift(); debugBytes -= item.bytes;
      if (debugDropped && debugQueue.length < DEBUG_MAX_EVENTS) {
        const dropped = debugDropped; debugDropped = 0;
        const summary: DebugEvent = { eventId: debugId(), event: "interrupted", topicKey: item.event.topicKey,
          videoId: item.event.videoId, subtitleKey: item.event.subtitleKey, positionMs: item.event.positionMs,
          text: `调试事件队列已丢弃 ${dropped} 条；日志不保证完整。` };
        await sendDebug(summary, item.revision);
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
  let timeout: ReturnType<typeof setTimeout> | undefined;
  inFlight.get(key)?.abort();
  inFlight.set(key, controller);
  try {
    const { origin } = await activeRoute();
    if (controller.signal.aborted) throw new Error("aborted");
    timeout = setTimeout(() => { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
    const gate = await readRuntimeStatus(controller.signal, origin);
    if (controller.signal.aborted) throw new Error("aborted");
    if (!gate.ok) {
      outcome = gate.reason;
      return { ok: false, reason: gate.reason === "protocol-mismatch" ? "runtime-incompatible" : gate.reason };
    }
    if (gate.status.reason === "INITIALIZING") {
      outcome = "runtime-initializing";
      return { ok: false, reason: "runtime-initializing" };
    }
    if (gate.status.reason === "SCHEMA_MISMATCH" || gate.status.reason === "IDENTITY_MISMATCH") {
      outcome = "runtime-incompatible";
      return { ok: false, reason: "runtime-incompatible" };
    }
    if (gate.status.mode !== "demo" && !gate.status.ready) {
      outcome = "backend_unavailable";
      return { ok: false, reason: "backend_unavailable" };
    }
    const storedPreferences = await readPreferenceSnapshot(origin);
    if (controller.signal.aborted) throw new Error("aborted");
    const reason = storedPreferences && typeof storedPreferences === "object" ? (storedPreferences as { reason?: string }).reason : undefined;
    const fetchedPreferences = storedPreferences && typeof storedPreferences === "object" && (storedPreferences as { ok?: boolean }).ok
      ? parsePreferences(storedPreferences) : undefined;
    const unavailable = reason === "network" || reason === "backend_unavailable";
    const preferences = fetchedPreferences ?? (unavailable ? preferencesFor(origin)?.value : undefined);
    if (!preferences && !unavailable) return { ok: false, reason: "invalid-response" };
    const requestPayload: CaptionHintRequest = payload;
    const response = await fetch(endpoint(origin, "caption-hints"), {
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
    if (timeout !== undefined) clearTimeout(timeout);
    if (inFlight.get(key) === controller) inFlight.delete(key);
  }
}

let applyingConnection = false;
async function applyConnection(origin: string) {
  if (applyingConnection) return { ok: false, reason: "busy" };
  applyingConnection = true;
  try {
    // 只探测候选的运行状态；连接和缓存直到成功保存才切换。
    const result = await readRuntimeStatus(undefined, origin);
    if (!result.ok) return result;
    if (!(result.status.ready || result.status.mode === "demo"))
      return { ok: false, reason: result.status.reason };
    const previous = await currentConfiguration();
    if (previous?.origin !== origin) {
      const value: Connection = { mode: "custom", origin };
      await chrome.storage.local.set({ [CONNECTION_KEY]: value });
      routeRevision++;
      configuration = Promise.resolve(value);
      cachedPreferences = undefined;
      for (const controller of inFlight.values()) controller.abort();
      void broadcastPreferenceChange("backend-connection-changed");
    }
    return { ...result, origin };
  } catch { return { ok: false, reason: "storage" }; }
  finally { applyingConnection = false; }
}

chrome.runtime.onMessage.addListener(
  (request: HintMessage | CancelMessage | RuntimeStatusMessage | ConnectionMessage | CaptionDebugMessage | { type: "local-preferences"; action: PreferenceAction; entryId?: string; lemma?: string; gloss?: string; commonWordCutoff?: number } |
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
    const popup = sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL("popup.html") && sender.tab === undefined;
    const manage = sender.id === chrome.runtime.id && isManagementUrl(sender.url, chrome.runtime.getURL("manage.html"));
    if (request?.type === "runtime-status") {
      if (!popup && !manage) { sendResponse({ ok: false, reason: "invalid-request" }); return undefined; }
      void activeRoute().then(async ({ origin }) => {
        const result = await readRuntimeStatus(undefined, origin);
        return { ...result, origin };
      }).then(sendResponse, () => sendResponse({ ok: false, reason: "network", origin: DEFAULT_ORIGIN }));
      return true;
    }
    if (request?.type === "backend-connection") {
      const origin = manage && Object.keys(request).length === 2 && loopbackOrigin(request.origin);
      if (!origin) { sendResponse({ ok: false, reason: "invalid-request" }); return undefined; }
      void applyConnection(origin).then(sendResponse);
      return true;
    }
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
      const params = new URLSearchParams({ query: request.query, sort: request.sort, page: String(request.page), pageSize: String(request.pageSize) });
      void localDataRoute().then(({ origin }) => boundedFetch(`${endpoint(origin, "lexicon")}?${params}`,
        { method: "POST", cache: "no-store", redirect: "error", credentials: "omit", referrerPolicy: "no-referrer" }))
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
      const permitted = (request.action === "read" && (manage || video)) ||
        (request.action === "setCutoff" && manage) ||
        ((request.action === "hide" || request.action === "restore") && (manage || video));
      if (!permitted) {
        sendResponse({ ok: false, reason: "invalid-request" }); return undefined;
      }
      const operation = request.action === "read" ? { action: "read" } as const
        : request.action === "setCutoff" && isCutoff(request.commonWordCutoff)
          ? { action: "setCutoff", commonWordCutoff: request.commonWordCutoff! } as const
        : request.action === "hide" && typeof request.entryId === "string"
          ? { action: "hide", entryId: request.entryId } as const
          : request.action === "restore" && typeof request.entryId === "string" ? { action: "restore", entryId: request.entryId } as const : undefined;
      if (!operation) { sendResponse({ ok: false, reason: "invalid-request" }); return undefined; }
      let operationPromise: Promise<unknown>;
      if (operation.action === "read") operationPromise = localDataRoute().then(({ origin }) => readPreferenceSnapshot(origin, true));
      else {
        const readsBeforeMutation = Promise.all([...pendingPreferenceReads.values()]);
        const revision = routeRevision;
        queuedPreferenceMutations++;
        operationPromise = preferenceMutationQueue.then(() => readsBeforeMutation).then(async () => {
          if (revision !== routeRevision) return { ok: false, reason: "network" };
          const { origin } = await localDataRoute();
          const value = await preferenceRequest(operation, origin);
          if ((value as { ok?: boolean })?.ok) cachePreferences(origin, revision, value);
          else if (revision === routeRevision && preferencesFor(origin)) cachedPreferences = undefined;
          return value;
        }).finally(() => { queuedPreferenceMutations = Math.max(0, queuedPreferenceMutations - 1); });
        preferenceMutationQueue = operationPromise.then(() => undefined, () => undefined);
      }
      void operationPromise.catch(() => ({ ok: false, reason: "backend_unavailable" })).then(value => {
        if ((request.action === "hide" || request.action === "restore" || request.action === "setCutoff") && (value as { ok?: boolean }).ok)
          void broadcastPreferenceChange();
        sendResponse(value);
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
