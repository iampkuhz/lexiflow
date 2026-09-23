export type CaptionSource = { caption: string; lineBreaks: number[] };

/** Keep source visual rows while using a space-normalized string for API UTF-16 offsets. */
export function readCaptionSource(segments: HTMLElement[]): CaptionSource | undefined {
  let caption = "";
  const lineBreaks: number[] = [];
  let previousRow: Element | null = null;
  let previousTop: number | undefined;
  for (const segment of segments) {
    const rows = (segment.innerText || segment.textContent || "").normalize("NFC").split(/\r?\n/);
    const row = segment.closest(".caption-visual-line");
    const top = segment.getBoundingClientRect().top;
    for (let index = 0; index < rows.length; index++) {
      const text = rows[index].replace(/\s+/g, " ").trim();
      if (!text) continue;
      if (caption) {
        caption += " ";
        const changedRow = row && previousRow ? row !== previousRow
          : previousTop !== undefined && Math.abs(top - previousTop) > 3;
        if (index > 0 || changedRow) lineBreaks.push(caption.length);
      }
      caption += text;
      previousRow = row;
      previousTop = top;
    }
  }
  return caption ? { caption, lineBreaks } : undefined;
}
