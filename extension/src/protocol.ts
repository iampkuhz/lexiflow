export const API_URL = "http://127.0.0.1:18080/api/v1/caption-hints";
export const MAX_CAPTION_LENGTH = 500;
export const REQUEST_TIMEOUT_MS = 1_500;

export type CaptionHintRequest = {
  contentId: string;
  contentRevision: number;
  segmentId: string;
  caption: string;
  startOffset: number;
  endOffset: number;
};

export type Hint = { chineseGloss: string };
export type HintResponse = { state: "READY" | "NO_PENDING"; hints: Hint[] };
export type ApiResult =
  | { ok: true; body: HintResponse }
  | { ok: false; reason: "aborted" | "invalid-request" | "network" | "rejected" | "invalid-response" };

export function isCaptionHintRequest(value: unknown): value is CaptionHintRequest {
  if (value === null || typeof value !== "object") return false;
  const request = value as Partial<CaptionHintRequest>;
  const { contentId, contentRevision, segmentId, caption, startOffset, endOffset } = request;
  return (
    typeof contentId === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(contentId) &&
    typeof contentRevision === "number" &&
    Number.isSafeInteger(contentRevision) &&
    contentRevision > 0 &&
    typeof segmentId === "string" &&
    /^[0-9a-f]{64}$/.test(segmentId) &&
    typeof caption === "string" &&
    caption.trim().length > 0 &&
    caption.length <= MAX_CAPTION_LENGTH &&
    typeof startOffset === "number" &&
    typeof endOffset === "number" &&
    Number.isSafeInteger(startOffset) &&
    Number.isSafeInteger(endOffset) &&
    startOffset >= 0 &&
    endOffset > startOffset &&
    endOffset <= caption.length
  );
}

export function parseHintResponse(value: unknown): HintResponse | undefined {
  if (value === null || typeof value !== "object") return undefined;
  const body = value as Partial<HintResponse>;
  if ((body.state !== "READY" && body.state !== "NO_PENDING") || !Array.isArray(body.hints)) return undefined;
  const hints: Hint[] = [];
  for (const hint of body.hints) {
    if (hint === null || typeof hint !== "object") return undefined;
    const gloss = (hint as Partial<Hint>).chineseGloss;
    if (typeof gloss !== "string" || gloss.trim().length === 0 || gloss.length > MAX_CAPTION_LENGTH) {
      return undefined;
    }
    hints.push({ chineseGloss: gloss });
  }
  return { state: body.state, hints };
}
