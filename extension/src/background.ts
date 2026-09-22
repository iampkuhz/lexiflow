import {
  API_URL,
  REQUEST_TIMEOUT_MS,
  isCaptionHintRequest,
  parseHintResponse,
  type ApiResult,
  type CaptionHintRequest
} from "./protocol";

type HintMessage = { type: "caption-hints"; requestId: string; payload: CaptionHintRequest };
type CancelMessage = { type: "cancel-caption-hint"; requestId: string };

const inFlight = new Map<string, AbortController>();

async function requestHints(key: string, payload: CaptionHintRequest): Promise<ApiResult> {
  if (!isCaptionHintRequest(payload)) {
    return { ok: false, reason: "invalid-request" };
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  inFlight.get(key)?.abort();
  inFlight.set(key, controller);
  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    if (!response.ok) return { ok: false, reason: "rejected" };
    const body = parseHintResponse(await response.json());
    return body === undefined ? { ok: false, reason: "invalid-response" } : { ok: true, body };
  } catch {
    return { ok: false, reason: controller.signal.aborted ? "aborted" : "network" };
  } finally {
    clearTimeout(timeout);
    if (inFlight.get(key) === controller) inFlight.delete(key);
  }
}

chrome.runtime.onMessage.addListener(
  (request: HintMessage | CancelMessage, sender, sendResponse: (response: ApiResult | { ok: true }) => void) => {
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
