declare const __LEXIFLOW_API_PORT__: number;
export const API_URL = `http://127.0.0.1:${typeof __LEXIFLOW_API_PORT__ === "number" ? __LEXIFLOW_API_PORT__ : 18080}/api/v1/caption-hints`;
export const MAX_CAPTION_LENGTH = 500;
export const MAX_PRESENTED_ENTRY_IDS = 128;
export const REQUEST_TIMEOUT_MS = 1_500;
export type PreviousSegment = { key: string; text: string };
export type CaptionSegment = PreviousSegment & { append: boolean; line: number };
export type CaptionSnapshot = { captions: { segments: CaptionSegment[] }[] };
export type CaptionHintRequest = { previousSegments: PreviousSegment[]; currentSnapshot: CaptionSnapshot };
export type KeyedHint = { startKey: string; endKey: string; startOffset: number; endOffset: number;
  chineseGloss: string; lexiconEntryId: string; frequencyRank: number | null };
export type Hint = Omit<KeyedHint, "startKey" | "endKey"> & { lexiconVersion: number };
export type HintResponse = { processedKeys: string[]; lexiconVersion: number | null; hints: KeyedHint[] };
export type ApiTimings = { query?: number; rules?: number; api?: number };
export type ApiResult = ({ ok: true; body: HintResponse; runtimeMode?: "formal" | "demo" } | { ok: false;
  reason: "timeout" | "aborted" | "invalid-request" | "network" | "rejected" | "backend_unavailable" | "invalid-response" | "runtime-initializing" | "runtime-incompatible" }) & { timings?: ApiTimings; requestId?: string };
export const snapshotSegments = <S extends CaptionSegment>(snapshot: { captions: { segments: S[] }[] }): S[] =>
  snapshot.captions.flatMap(group => group.segments);
export const snapshotText = (snapshot: CaptionSnapshot): string => snapshotSegments(snapshot).map(segment => segment.text).join("");
const boundedId = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 128;
const nonnegative = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
export const isFrequencyRank = (value: unknown): value is number | null => value === null || (nonnegative(value) && value > 0);
function exactFields(value: unknown, keys: readonly string[]): boolean {
  return !!value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function boundedSegmentList(segments: PreviousSegment[]): boolean {
  const keys = new Set<string>(); let length = 0;
  for (const segment of segments) {
    if (!segment || !boundedId(segment.key) || keys.has(segment.key) ||
        typeof segment.text !== "string" || !segment.text.length || segment.text.length > MAX_CAPTION_LENGTH ||
        Array.from(segment.text).some(character => character.length === 1 && character.charCodeAt(0) >= 0xd800 && character.charCodeAt(0) <= 0xdfff)) return false;
    length += segment.text.length; keys.add(segment.key);
    if (length > MAX_CAPTION_LENGTH || keys.size > 500) return false;
  }
  return true;
}
/** 正 BIGINT 以十进制字符串传输；不转换为丢失精度的 Number。 */
export function isEntryId(value: unknown): value is string {
  return typeof value === "string" && /^[1-9][0-9]{0,18}$/.test(value) &&
    (value.length < 19 || value <= "9223372036854775807");
}
export function isStableId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
}
function isCaptionSnapshot(value: unknown): value is CaptionSnapshot {
  if (!exactFields(value, ["captions"])) return false;
  const groups = (value as CaptionSnapshot).captions;
  if (!Array.isArray(groups) || groups.length > 500) return false;
  const segments: CaptionSegment[] = [];
  for (const group of groups) {
    if (!exactFields(group, ["segments"]) || !Array.isArray(group.segments) || !group.segments.length ||
        group.segments.length + segments.length > 500) return false;
    for (const segment of group.segments) {
      if (!exactFields(segment, ["key", "text", "append", "line"]) ||
          typeof segment.append !== "boolean" || !nonnegative(segment.line)) return false;
      segments.push(segment);
    }
  }
  return boundedSegmentList(segments);
}
export function isCaptionHintRequest(value: unknown): value is CaptionHintRequest {
  if (!exactFields(value, ["previousSegments", "currentSnapshot"])) return false;
  const request = value as CaptionHintRequest;
  if (!Array.isArray(request.previousSegments) || request.previousSegments.length > 500 ||
      !request.previousSegments.every(segment => exactFields(segment, ["key", "text"])) ||
      !boundedSegmentList(request.previousSegments) || !isCaptionSnapshot(request.currentSnapshot)) return false;
  const previous = new Map(request.previousSegments.map(segment => [segment.key, segment.text]));
  return snapshotSegments(request.currentSnapshot).every(segment => !previous.has(segment.key) || previous.get(segment.key)!.endsWith(segment.text));
}
export function splitsSurrogate(text: string, offset: number): boolean {
  return offset > 0 && offset < text.length && /[\uD800-\uDBFF]/.test(text[offset - 1]) && /[\uDC00-\uDFFF]/.test(text[offset]);
}
/** 只允许同组同视觉行至多两个紧邻旧尾片段补全新提示；提示终点必须属于本次新增范围。 */
export function resolveHint(hint: KeyedHint, snapshot: CaptionSnapshot, lexiconVersion: number): Hint | undefined {
  let position = 0;
  for (const group of snapshot.captions) {
    const start = group.segments.findIndex(segment => segment.key === hint.startKey);
    const end = group.segments.findIndex(segment => segment.key === hint.endKey);
    if (start >= 0 && end >= start) {
      const first = group.segments[start], last = group.segments[end];
      const span = group.segments.slice(start, end + 1);
      const firstNew = span.findIndex(segment => segment.append);
      const oldPrefix = firstNew < 0 ? span : span.slice(0, firstNew);
      if (firstNew < 0 || !span.every(segment => segment.line === first.line) ||
          !span.slice(firstNew).every(segment => segment.append) ||
          oldPrefix.length > 2 || oldPrefix.reduce((sum, segment) => sum + segment.text.length, 0) > 48 ||
          !nonnegative(hint.startOffset) || !nonnegative(hint.endOffset) || hint.startOffset >= first.text.length ||
          hint.endOffset < 1 || hint.endOffset > last.text.length || splitsSurrogate(first.text, hint.startOffset) || splitsSurrogate(last.text, hint.endOffset)) return;
      const startOffset = position + group.segments.slice(0, start).reduce((sum, segment) => sum + segment.text.length, 0) + hint.startOffset;
      const endOffset = position + group.segments.slice(0, end).reduce((sum, segment) => sum + segment.text.length, 0) + hint.endOffset;
      if (endOffset <= startOffset) return;
      return { startOffset, endOffset, chineseGloss: hint.chineseGloss, lexiconEntryId: hint.lexiconEntryId,
        lexiconVersion, frequencyRank: hint.frequencyRank };
    }
    position += group.segments.reduce((sum, segment) => sum + segment.text.length, 0);
  }
}
/** 严格校验处理覆盖及词库证据；成功且无提示也必须确认所有待处理 key。 */
export function parseHintResponse(value: unknown, request: CaptionHintRequest): HintResponse | undefined {
  if (!exactFields(value, ["processedKeys", "lexiconVersion", "hints"]) || !isCaptionHintRequest(request)) return;
  const body = value as HintResponse;
  const expected = snapshotSegments(request.currentSnapshot).filter(segment => segment.append).map(segment => segment.key);
  if (!Array.isArray(body.processedKeys) || JSON.stringify(body.processedKeys) !== JSON.stringify(expected) ||
      !Array.isArray(body.hints) || body.hints.length > MAX_CAPTION_LENGTH) return;
  const version = body.lexiconVersion;
  if (version === null ? body.hints.length > 0 : !nonnegative(version) || version < 1) return;
  let end = 0;
  const ranks = new Map<number, string>();
  const entries = new Map<string, number | null>();
  for (const hint of body.hints) {
    if (!exactFields(hint, ["startKey", "endKey", "startOffset", "endOffset", "chineseGloss", "lexiconEntryId", "frequencyRank"]) ||
        !isEntryId(hint.lexiconEntryId) || !isFrequencyRank(hint.frequencyRank) || typeof hint.chineseGloss !== "string" ||
        !hint.chineseGloss.length || Array.from(hint.chineseGloss).length > 24 || !/\p{Script=Han}/u.test(hint.chineseGloss) || /[\s\p{P}\p{S}\p{C}]/u.test(hint.chineseGloss)) return;
    const resolved = version === null ? undefined : resolveHint(hint, request.currentSnapshot, version);
    if (!resolved || resolved.startOffset < end) return;
    const { lexiconEntryId, frequencyRank } = hint;
    if (entries.has(lexiconEntryId) && entries.get(lexiconEntryId) !== frequencyRank) return;
    if (frequencyRank !== null) {
      const owner = ranks.get(frequencyRank);
      if (owner !== undefined && owner !== lexiconEntryId) return;
      ranks.set(frequencyRank, lexiconEntryId);
    }
    entries.set(lexiconEntryId, frequencyRank);
    end = resolved.endOffset;
  }
  return { processedKeys: [...body.processedKeys], lexiconVersion: version, hints: body.hints.map(hint => ({ ...hint })) };
}
