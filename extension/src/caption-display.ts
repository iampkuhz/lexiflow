/** Local presentation settings, independent of the service and known word identities. */
export const CAPTION_LINE_LIMIT_KEY = "captionLineLimit";
export const MIN_CAPTION_LINE_LIMIT = 2;
export const DEFAULT_CAPTION_LINE_LIMIT = 3;
export type CaptionLineLimit = 2 | 3 | 4;
export function captionLineLimit(value: unknown): CaptionLineLimit {
  return value === 2 || value === 3 || value === 4 ? value : DEFAULT_CAPTION_LINE_LIMIT;
}

/** Retain the contiguous newest suffix which fits; never skip a recent row to make room for an older one. */
export function fitCaptionHistory<T>(past: T[], slots: number, visualLines: (row: T) => number): T[] {
  let start = past.length;
  while (start > 0) {
    const size = visualLines(past[start - 1]);
    if (size < 1 || size > slots) break;
    slots -= size; start--;
  }
  return past.slice(start);
}
