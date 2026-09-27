export type CaptionSource = { caption: string; lineBreaks: number[]; windowBreaks?: number[] };

/** Keep source visual rows while using a space-normalized string for API UTF-16 offsets. */
export function readCaptionSource(segments: HTMLElement[]): CaptionSource | undefined {
  let caption = "";
  const lineBreaks: number[] = [];
  let previousRow: Element | null = null;
  let previousTop: number | undefined;
  let previousWindow: Element | null = null;
  const windowBreaks: number[] = [];
  for (const segment of segments) {
    const rows = (segment.innerText || segment.textContent || "").normalize("NFC").split(/\r?\n/);
    const row = segment.closest(".caption-visual-line");
    const window = segment.closest(".caption-window");
    const top = segment.getBoundingClientRect().top;
    for (let index = 0; index < rows.length; index++) {
      const text = rows[index].replace(/\s+/g, " ").trim();
      if (!text) continue;
      if (caption) {
        caption += " ";
        if (index === 0 && window && previousWindow && window !== previousWindow) windowBreaks.push(caption.length - 1);
        const changedRow = row && previousRow ? row !== previousRow
          : previousTop !== undefined && Math.abs(top - previousTop) > 3;
        if (index > 0 || changedRow) lineBreaks.push(caption.length);
      }
      caption += text;
      previousRow = row;
      previousTop = top;
      previousWindow = window;
    }
  }
  return caption ? { caption, lineBreaks, ...(windowBreaks.length ? { windowBreaks } : {}) } : undefined;
}
