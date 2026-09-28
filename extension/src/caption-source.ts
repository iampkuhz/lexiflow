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

export type ViewportRow = { id: object; text: string; top: number; bottom: number };
/** 同一原生窗口内只向前滚动；只保留仍在 DOM 中且内容未变的行身份。 */
export class CaptionRowViewport {
  private previous: ViewportRow[] = [];
  private retired = new Map<object, string>();

  select(rows: ViewportRow[], top: number, bottom: number): object[] {
    const unchanged = (row: ViewportRow) => rows.some(next => next.id === row.id && next.text === row.text);
    for (const [id, text] of this.retired) {
      if (!rows.some(row => row.id === id && row.text === text)) this.retired.delete(id);
    }
    const visible = rows.filter(row => row.bottom > top + 1 && row.top < bottom - 1);
    const retained = this.previous.filter(unchanged);
    const continuous = retained.some(row => visible.some(next => next.id === row.id));
    // transform 复位早于旧行移除时，已展示的新尾行可能暂时落在裁剪区下方。
    // 只保留仍有可见交集的同一批节点，不从字幕历史或未知离屏文字补全。
    const candidates = rows.filter(row => !this.retired.has(row.id) &&
      (visible.includes(row) || (continuous && row.top >= bottom - 1 && retained.some(old => old.id === row.id))));
    const selected: ViewportRow[] = [];
    let height = 0;
    for (let index = candidates.length - 1; index >= 0; index--) {
      const row = candidates[index], rowHeight = Math.max(0, row.bottom - row.top);
      if (selected.length && height + rowHeight > bottom - top + 1) break;
      selected.unshift(row); height += rowHeight;
    }
    if (selected.length && continuous) {
      const first = rows.findIndex(row => row.id === selected[0].id);
      for (const row of retained) {
        if (rows.findIndex(next => next.id === row.id) < first) this.retired.set(row.id, row.text);
      }
    }
    this.previous = selected;
    return selected.map(row => row.id);
  }
}

/** 可见窗口适配：不跨换源保存行，不修改原生 DOM，也不使用时间 debounce。 */
export class CaptionViewport {
  private windows = new Map<HTMLElement, CaptionRowViewport>();
  reset(): void { this.windows.clear(); }
  read(player: HTMLElement): HTMLElement[] {
    const all = Array.from(player.querySelectorAll<HTMLElement>(".ytp-caption-segment"))
      .filter(segment => segment.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }));
    const grouped = new Map<HTMLElement, HTMLElement[]>();
    const accepted = new Set<HTMLElement>();
    for (const segment of all) {
      const window = segment.closest<HTMLElement>(".caption-window");
      if (!window || !clipsCaptionRows(window)) { accepted.add(segment); continue; }
      const members = grouped.get(window) ?? []; members.push(segment); grouped.set(window, members);
    }
    for (const window of this.windows.keys()) if (!grouped.has(window)) this.windows.delete(window);
    for (const [window, members] of grouped) {
      const rowMembers = new Map<HTMLElement, HTMLElement[]>();
      for (const segment of members) {
        const row = segment.closest<HTMLElement>(".caption-visual-line") ?? segment;
        const parts = rowMembers.get(row) ?? []; parts.push(segment); rowMembers.set(row, parts);
      }
      const rows = [...rowMembers].map(([id, parts]) => {
        const box = id.getBoundingClientRect();
        return { id, text: parts.map(part => part.textContent ?? "").join("\u0000"), top: box.top, bottom: box.bottom };
      });
      const state = this.windows.get(window) ?? new CaptionRowViewport(); this.windows.set(window, state);
      const bounds = window.getBoundingClientRect();
      for (const id of state.select(rows, bounds.top, bounds.bottom)) {
        for (const segment of rowMembers.get(id as HTMLElement) ?? []) accepted.add(segment);
      }
    }
    return all.filter(segment => accepted.has(segment));
  }
}

export function clipsCaptionRows(window: HTMLElement): boolean {
  const style = getComputedStyle(window);
  return [style.overflow, style.overflowY].some(value => value === "hidden" || value === "clip");
}
