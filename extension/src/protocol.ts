declare const __LEXIFLOW_API_PORT__: number;
export const API_URL = `http://127.0.0.1:${typeof __LEXIFLOW_API_PORT__ === "number" ? __LEXIFLOW_API_PORT__ : 18080}/api/v1/caption-hints`;
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

export type Hint = { startOffset: number; endOffset: number; chineseGloss: string;
  lexiconEntryId: string; lexiconVersion: number; senseId: string };
/** Published identities are canonical UUIDs, not guessed terms or array indices. */
export function isStableId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
}
export type HintResponse = { caption: string; state: "READY" | "NO_PENDING"; hints: Hint[] };
export type ApiTimings = { query?: number; rules?: number; api?: number };
export type ApiResult = (
  | { ok: true; body: HintResponse }
  | { ok: false; reason: "timeout" | "aborted" | "invalid-request" | "network" | "rejected" | "invalid-response" }
) & { timings?: ApiTimings };

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

function splitsSurrogate(text: string, offset: number): boolean {
  return offset > 0 && offset < text.length &&
    /[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset]);
}

/** 校验 API 的确定性词库提示，不在客户端推断或改写词义。 */
export function parseHintResponse(value: unknown, expectedCaption?: string): HintResponse | undefined {
  if (value === null || typeof value !== "object") return undefined;
  const body = value as Partial<HintResponse>;
  if (typeof body.caption !== "string" || body.caption.length === 0 || body.caption.length > MAX_CAPTION_LENGTH ||
      (expectedCaption !== undefined && body.caption !== expectedCaption) ||
      (body.state !== "READY" && body.state !== "NO_PENDING") || !Array.isArray(body.hints)) return undefined;
  if ((body.state === "READY" && (body.hints.length === 0 || body.hints.length > 3)) ||
      (body.state === "NO_PENDING" && body.hints.length !== 0)) return undefined;
  const hints: Hint[] = [];
  for (const hint of body.hints) {
    if (hint === null || typeof hint !== "object") return undefined;
    const { startOffset, endOffset, chineseGloss, lexiconEntryId, lexiconVersion, senseId } = hint as Hint;
    if (!isStableId(lexiconEntryId) || !isStableId(senseId) || !Number.isSafeInteger(lexiconVersion) || lexiconVersion < 1) return undefined;
    if (!Number.isSafeInteger(startOffset) || !Number.isSafeInteger(endOffset) ||
        startOffset < 0 || endOffset <= startOffset || endOffset > body.caption.length ||
        splitsSurrogate(body.caption, startOffset) || splitsSurrogate(body.caption, endOffset) ||
        typeof chineseGloss !== "string" || chineseGloss.trim().length === 0) return undefined;
    // Reject doubtful evidence intact. Taking the first clause would invent a sense choice.
    if (Array.from(chineseGloss).length > 24 || !/\p{Script=Han}/u.test(chineseGloss) ||
        /[\s\p{P}\p{S}\p{C}]/u.test(chineseGloss)) return undefined;
    hints.push({ startOffset, endOffset, chineseGloss, lexiconEntryId, lexiconVersion, senseId });
  }
  if (new Set(hints.map(hint => hint.lexiconVersion)).size > 1) return undefined;
  hints.sort((left, right) => left.startOffset - right.startOffset);
  if (hints.some((hint, index) => index > 0 && hint.startOffset < hints[index - 1].endOffset)) return undefined;
  return { caption: body.caption, state: body.state, hints };
}

/** Preserve the original characters and punctuation, adding glosses after their bound spans. */
export function inlineParts(caption: string, hints: Hint[]): { text: string; gloss: boolean }[] {
  const parts: { text: string; gloss: boolean }[] = [];
  let offset = 0;
  for (const hint of hints) {
    parts.push({ text: caption.slice(offset, hint.endOffset), gloss: false });
    parts.push({ text: `(${hint.chineseGloss})`, gloss: true });
    offset = hint.endOffset;
  }
  parts.push({ text: caption.slice(offset), gloss: false });
  return parts;
}
