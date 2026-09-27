declare const __LEXIFLOW_API_PORT__: number;
export const API_URL = `http://127.0.0.1:${typeof __LEXIFLOW_API_PORT__ === "number" ? __LEXIFLOW_API_PORT__ : 18080}/api/v1/caption-hints`;
export const MAX_CAPTION_LENGTH = 500;
export const REQUEST_TIMEOUT_MS = 1_500;
export type CaptionSegment = { key: string; text: string; offsetMs: number | null; append: boolean; line: number };
export type CaptionGroup = { windowId: string | null; startMs: number | null; segments: CaptionSegment[] };
export type CaptionSnapshot = { captions: CaptionGroup[] };
export type CaptionHintRequest = { captionTopicKey: string; trackKey: string | null;
  lastRequestedSnapshot: CaptionSnapshot | null; currentSnapshot: CaptionSnapshot };
export type Hint = { startOffset: number; endOffset: number; chineseGloss: string;
  lexiconEntryId: string; lexiconVersion: number; senseId: string };
export type KeyedHint = Hint & { startKey: string; endKey: string };
export type HintResponse = { processedKeys: string[]; hints: KeyedHint[] };
export type ApiTimings = { query?: number; rules?: number; api?: number };
export type ApiResult = ({ ok: true; body: HintResponse } | { ok: false;
  reason: "timeout" | "aborted" | "invalid-request" | "network" | "rejected" | "invalid-response" }) & { timings?: ApiTimings };
export const snapshotSegments = (snapshot: CaptionSnapshot): CaptionSegment[] => snapshot.captions.flatMap(group => group.segments);
export const snapshotText = (snapshot: CaptionSnapshot): string => snapshotSegments(snapshot).map(segment => segment.text).join("");
const boundedId = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 128;
const nonnegative = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
export function isStableId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
}
export function isCaptionSnapshot(value: unknown): value is CaptionSnapshot {
  if (!value || typeof value !== "object") return false;
  const groups = (value as CaptionSnapshot).captions;
  if (!Array.isArray(groups) || groups.length > 500) return false;
  let length = 0; const keys = new Set<string>();
  for (const group of groups) {
    if (!group || (group.windowId !== null && !boundedId(group.windowId)) ||
        (group.startMs !== null && !nonnegative(group.startMs)) || !Array.isArray(group.segments) || !group.segments.length) return false;
    for (const segment of group.segments) {
      if (!segment || !boundedId(segment.key) || keys.has(segment.key) || typeof segment.text !== "string" || !segment.text.length ||
          Array.from(segment.text).some(character => character.length === 1 && character.charCodeAt(0) >= 0xd800 && character.charCodeAt(0) <= 0xdfff) ||
          typeof segment.append !== "boolean" || !nonnegative(segment.line) ||
          (segment.offsetMs !== null && !nonnegative(segment.offsetMs))) return false;
      length += segment.text.length; keys.add(segment.key);
      if (length > MAX_CAPTION_LENGTH || keys.size > 500) return false;
    }
  }
  return true;
}
export function isCaptionHintRequest(value: unknown): value is CaptionHintRequest {
  if (!value || typeof value !== "object") return false;
  const request = value as CaptionHintRequest;
  if (!boundedId(request.captionTopicKey) || (request.trackKey !== null && !boundedId(request.trackKey)) ||
      !isCaptionSnapshot(request.currentSnapshot) || (request.lastRequestedSnapshot !== null && !isCaptionSnapshot(request.lastRequestedSnapshot))) return false;
  const previous = new Map(request.lastRequestedSnapshot ? snapshotSegments(request.lastRequestedSnapshot).map(segment => [segment.key, segment.text]) : []);
  return snapshotSegments(request.currentSnapshot).every(segment => !previous.has(segment.key) || previous.get(segment.key)!.endsWith(segment.text));
}
export function splitsSurrogate(text: string, offset: number): boolean {
  return offset > 0 && offset < text.length && /[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset]);
}
/** 将片段内范围解析成发出快照内的位置，拒绝跨旧片段或字幕组的提示。 */
export function resolveHint(hint: KeyedHint, snapshot: CaptionSnapshot): Hint | undefined {
  let position = 0;
  for (const group of snapshot.captions) {
    const start = group.segments.findIndex(segment => segment.key === hint.startKey);
    const end = group.segments.findIndex(segment => segment.key === hint.endKey);
    if (start >= 0 && end >= start) {
      const first = group.segments[start], last = group.segments[end];
      if (!group.segments.slice(start, end + 1).every(segment => segment.append) ||
          !nonnegative(hint.startOffset) || !nonnegative(hint.endOffset) || hint.startOffset >= first.text.length ||
          hint.endOffset < 1 || hint.endOffset > last.text.length || splitsSurrogate(first.text, hint.startOffset) || splitsSurrogate(last.text, hint.endOffset)) return;
      const startOffset = position + group.segments.slice(0, start).reduce((sum, segment) => sum + segment.text.length, 0) + hint.startOffset;
      const endOffset = position + group.segments.slice(0, end).reduce((sum, segment) => sum + segment.text.length, 0) + hint.endOffset;
      if (endOffset <= startOffset) return;
      return { startOffset, endOffset, chineseGloss: hint.chineseGloss, lexiconEntryId: hint.lexiconEntryId,
        lexiconVersion: hint.lexiconVersion, senseId: hint.senseId };
    }
    position += group.segments.reduce((sum, segment) => sum + segment.text.length, 0);
  }
}
/** 严格校验处理覆盖及词库证据；成功且无提示也必须确认所有待处理 key。 */
export function parseHintResponse(value: unknown, request: CaptionHintRequest): HintResponse | undefined {
  if (!value || typeof value !== "object" || !isCaptionHintRequest(request)) return;
  const body = value as HintResponse;
  const expected = snapshotSegments(request.currentSnapshot).filter(segment => segment.append).map(segment => segment.key);
  if (!Array.isArray(body.processedKeys) || JSON.stringify(body.processedKeys) !== JSON.stringify(expected) ||
      !Array.isArray(body.hints) || body.hints.length > MAX_CAPTION_LENGTH) return;
  let end = 0; let version: number | undefined;
  for (const hint of body.hints) {
    if (!hint || !isStableId(hint.lexiconEntryId) || !isStableId(hint.senseId) || !Number.isSafeInteger(hint.lexiconVersion) || hint.lexiconVersion < 1 ||
        (version !== undefined && version !== hint.lexiconVersion) || typeof hint.chineseGloss !== "string" ||
        !hint.chineseGloss.length || Array.from(hint.chineseGloss).length > 24 || !/\p{Script=Han}/u.test(hint.chineseGloss) || /[\s\p{P}\p{S}\p{C}]/u.test(hint.chineseGloss)) return;
    const resolved = resolveHint(hint, request.currentSnapshot);
    if (!resolved || resolved.startOffset < end) return;
    end = resolved.endOffset; version = hint.lexiconVersion;
  }
  return { processedKeys: [...body.processedKeys], hints: body.hints.map(hint => ({ ...hint })) };
}
export function inlineParts(caption: string, hints: Hint[]): { text: string; gloss: boolean }[] {
  const parts: { text: string; gloss: boolean }[] = []; let offset = 0;
  for (const hint of hints) {
    parts.push({ text: caption.slice(offset, hint.endOffset), gloss: false }, { text: `(${hint.chineseGloss})`, gloss: true }); offset = hint.endOffset;
  }
  parts.push({ text: caption.slice(offset), gloss: false }); return parts;
}
