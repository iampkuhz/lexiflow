import type { CaptionSnapshot, CaptionSegment, Hint } from "./protocol";
import { snapshotSegments, snapshotText, splitsSurrogate } from "./protocol";
import type { CaptionSource } from "./caption-source";
export type SourceMetadata = { windowId: string | null; startMs: number | null; offsetMs: number | null };
/** 优先完整保留旧前缀，其次取最长旧后缀与新前缀；不以 DOM 身份判断字幕边界。 */
export function retainedOverlap(previous: string, current: string): number {
  for (let size = Math.min(previous.length, current.length); size > 0; size--) {
    if (!splitsSurrogate(previous, previous.length - size) && !splitsSurrogate(current, size) && previous.endsWith(current.slice(0, size))) return size;
  }
  return 0;
}
/** 只迁移仍完整可见且词边界未失效的提示。 */
export function remapHints(previous: string, current: string, hints: Hint[]): Hint[] {
  const overlap = retainedOverlap(previous, current), removed = previous.length - overlap;
  return hints.flatMap(hint => {
    if (hint.startOffset < removed || hint.endOffset > previous.length) return [];
    const startOffset = hint.startOffset - removed, endOffset = hint.endOffset - removed;
    if (endOffset > overlap || /[\p{L}\p{N}_]/u.test(current.slice(Math.max(0, startOffset - 1), startOffset)) ||
        /[\p{L}\p{N}_]/u.test(current.slice(endOffset, endOffset + 1))) return [];
    return [{ ...hint, startOffset, endOffset }];
  });
}
/** 页面内 key 不复用；保留段保持身份，新增后缀永远分配新身份。 */
export class CaptionSnapshotTracker {
  private nextId = 0;
  private previous: CaptionSnapshot = { captions: [] };
  constructor(private readonly prefix: string = crypto.randomUUID()) {}
  reset(): void { this.previous = { captions: [] }; }
  capture(source: CaptionSource, metadata: (text: string, offset: number) => SourceMetadata = () => ({ windowId: null, startMs: null, offsetMs: null })): CaptionSnapshot {
    const oldText = snapshotText(this.previous), overlap = retainedOverlap(oldText, source.caption);
    const removed = oldText.length - overlap;
    const kept: CaptionSegment[] = []; let offset = 0;
    for (const segment of snapshotSegments(this.previous)) {
      const end = offset + segment.text.length;
      if (end > removed) kept.push({ ...segment, text: segment.text.slice(Math.max(0, removed - offset)), append: false });
      offset = end;
    }
    if (!overlap) kept.length = 0;
    const suffix = source.caption.slice(overlap);
    for (const match of suffix.matchAll(/\s*\S+|\s+$/gu)) {
      kept.push({ key: `${this.prefix}-${++this.nextId}`, text: match[0], offsetMs: null, append: true, line: 0 });
    }
    const captions: CaptionSnapshot["captions"] = []; offset = 0; let lastWindow = -1;
    for (const segment of kept) {
      const info = metadata(segment.text, offset);
      const firstText = offset + (segment.text.match(/^\s*/u)?.[0].length ?? 0);
      const window = (source.windowBreaks ?? []).filter(value => value <= offset).length;
      const windowStart = window ? source.windowBreaks![window - 1] + 1 : 0;
      segment.line = source.lineBreaks.filter(value => value > windowStart && value <= firstText).length;
      segment.offsetMs = info.offsetMs;
      let group = captions.at(-1);
      if (!group || window !== lastWindow || group.windowId !== info.windowId || group.startMs !== info.startMs) {
        group = { windowId: info.windowId, startMs: info.startMs, segments: [] }; captions.push(group);
      }
      group.segments.push(segment); offset += segment.text.length; lastWindow = window;
    }
    this.previous = { captions };
    return structuredClone(this.previous);
  }
}
