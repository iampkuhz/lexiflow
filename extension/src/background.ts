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

async function requestHints(requestId: string, payload: CaptionHintRequest): Promise<ApiResult> {
  if (!isCaptionHintRequest(payload) || requestId.length === 0 || requestId.length > 128) {
    return { ok: false, reason: "invalid-request" };
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  inFlight.set(requestId, controller);
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
    if (inFlight.get(requestId) === controller) inFlight.delete(requestId);
  }
}

chrome.runtime.onMessage.addListener(
  (request: HintMessage | CancelMessage, _sender, sendResponse: (response: ApiResult | { ok: true }) => void) => {
    if (request?.type === "cancel-caption-hint") {
      inFlight.get(request.requestId)?.abort();
      sendResponse({ ok: true });
      return undefined;
    }
    if (request?.type !== "caption-hints") return undefined;
    void requestHints(request.requestId, request.payload).then(sendResponse);
    return true;
  }
);
