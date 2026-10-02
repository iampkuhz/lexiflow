export type DebugRenderedLine = {
  start: number;
  end: number;
  caption: string;
  hints: Array<{ startOffset: number; endOffset: number; chineseGloss: string }>;
};
export type DebugRange = { start: number; end: number };

/** Build only the newly processed displayed caption slice, retaining old text only when a visible hint crosses its boundary. */
export function extractIncrementalText(line: DebugRenderedLine, appendedRanges: DebugRange[]): string | undefined {
  const pieces: string[] = [];
  for (const range of appendedRanges) {
    if (range.end <= line.start || range.start >= line.end) continue;
    let start = Math.max(line.start, range.start), end = Math.min(line.end, range.end);
    for (const hint of line.hints) {
      if (hint.startOffset < start && hint.endOffset > start) start = Math.max(line.start, hint.startOffset);
      if (hint.startOffset < end && hint.endOffset > end) end = Math.min(line.end, hint.endOffset);
    }
    let cursor = start, text = "";
    for (const hint of line.hints.filter(item => item.startOffset >= start && item.endOffset <= end)
      .sort((left, right) => left.startOffset - right.startOffset)) {
      text += line.caption.slice(cursor - line.start, hint.endOffset - line.start) + `(${hint.chineseGloss})`;
      cursor = hint.endOffset;
    }
    text += line.caption.slice(cursor - line.start, end - line.start);
    if (text) pieces.push(text);
  }
  const text = pieces.join("\n");
  return text && text.length <= 16384 ? text : undefined;
}

export function rememberBounded<T>(values: Set<T>, value: T, maximum: number): void {
  values.add(value);
  while (values.size > maximum) values.delete(values.values().next().value!);
}
