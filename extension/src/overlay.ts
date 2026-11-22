import { snapshotText, snapshotSegments, type Hint } from "./protocol";
import type { StreamView } from "./stream";
import type { Diagnostics } from "./diagnostics";
import { DEFAULT_CAPTION_LINE_LIMIT, MIN_CAPTION_LINE_LIMIT, fitCaptionHistory, type CaptionLineLimit } from "./caption-display";
import { clipsCaptionRows, type CaptionSource } from "./caption-source";
import type { VocabularyObservation } from "./hint-vocabulary";

export const OVERLAY_ID = "lexiflow-caption-overlay";
export const captionSelector = ".ytp-caption-segment";
const HISTORY_HANDOFF_MS = 180;
const BATCH_STEP_MS = 120;
const BATCH_HOLD_MS = 75;
const FAST_BATCH_ENTER_REDUCTION_MS = 24;
const FAST_BATCH_TAIL_REDUCTION_MS = 45;
export function visibleSegments(player: HTMLElement): HTMLElement[] {
  return Array.from(player.querySelectorAll<HTMLElement>(captionSelector))
    .filter(segment => {
      if (!segment.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
      const row = segment.closest<HTMLElement>(".caption-visual-line") ?? segment;
      const window = segment.closest<HTMLElement>(".caption-window");
      if (!window) return true;
      const style = getComputedStyle(window);
      if (![style.overflow, style.overflowY].some(value => value === "hidden" || value === "clip")) return true;
      const bounds = window.getBoundingClientRect(), box = row.getBoundingClientRect();
      return box.bottom > bounds.top + 1 && box.top < bounds.bottom - 1;
    });
}
export type PreferenceView = { ready: boolean; hiddenIds: Set<string>; message: string };
export type PresentedLine = { subtitleKey: string; segmentKeys: string[]; positionMs: number | null; text: string };
export type RenderedLine = PresentedLine & { start: number; end: number; caption: string; hints: Array<{ startOffset: number; endOffset: number; chineseGloss: string }> };
export type OverlayPresentation = { shown: number; lines: RenderedLine[]; hintEntryIds: string[] };

/** Rendering owns no network or storage; only explicit button activation changes preferences. */
export class BilingualOverlay {
  private host?: HTMLElement;
  private line?: HTMLSpanElement;
  private status?: HTMLSpanElement;
  private viewport?: HTMLElement;
  private outgoing?: HTMLElement;
  private singleWindow = false;
  private lineLimit: CaptionLineLimit = DEFAULT_CAPTION_LINE_LIMIT;
  private lineLimitChanged = false;
  private cueLayout?: { text: string; cut: number; width: number; font: string; shared: boolean; compact: boolean };
  private nativeRows: string[] = [];
  private fittedGeometry = "";
  private textWidth?: CanvasRenderingContext2D;
  private readonly rowSourceText = new WeakMap<HTMLElement, string>();
  private gapTimer?: ReturnType<typeof setTimeout>;
  private motionUntil = 0;
  private motionScale = 1;
  private timelineRate = 1;
  private motionTimer?: ReturnType<typeof setTimeout>;
  private boundedBatch = false;
  private rowAnimations: Animation[] = [];
  private player?: HTMLElement;
  private shownHintKeys = new Set<string>();
  private reportedPresentationHints = new Set<string>();
  private presented = new Map<string, PresentedLine>();
  private lastPresentation: OverlayPresentation = { shown: 0, lines: [], hintEntryIds: [] };

  constructor(private readonly suppress: (entryId: string) => void,
    private readonly onPresentationExit: (line: PresentedLine) => void = () => undefined,
    private readonly observeVocabulary: (segments: ReadonlySet<string>, terms: VocabularyObservation[]) => void = () => undefined) {}

  // Raw DOM can briefly go empty while YouTube replaces a window. Keep only the
  // current frame for a bounded handoff; lifecycle invalidations still clear immediately.
  bridgeSourceGap(): void {
    if (this.gapTimer !== undefined) return;
    const line = this.line;
    const usesHistory = this.capacityHasHistory;
    if (!line?.textContent || (!usesHistory && (!this.singleWindow || this.visualLineCount(line) < 1.8)) ||
        window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      this.clear(); return;
    }
    this.gapTimer = setTimeout(() => this.clear(), usesHistory ? HISTORY_HANDOFF_MS : 48);
  }
  setLineLimit(limit: CaptionLineLimit): boolean {
    if (limit === this.lineLimit) return false;
    this.lineLimit = limit; this.lineLimitChanged = true;
    this.updateHeightLimit();
    return true;
  }
  private get capacityHasHistory(): boolean { return this.lineLimit > MIN_CAPTION_LINE_LIMIT; }
  private updateHeightLimit(): void {
    if (!this.viewport || !this.line) return;
    const size = Number.parseFloat(this.line.style.fontSize);
    const max = this.capacityHasHistory && size > 0 ? `${Math.round(size * 1.55 * this.lineLimit * 1000) / 1000}px` : "";
    if (this.viewport.style.maxHeight !== max) this.viewport.style.maxHeight = max;
  }
  get bridgingGap(): boolean { return this.gapTimer !== undefined; }
  clear(): void { this.render({ state: "idle" }, { ready: false, hiddenIds: new Set(), message: "" }); }
  dispose(): void {
    if (this.gapTimer !== undefined) clearTimeout(this.gapTimer);
    this.gapTimer = undefined;
    this.finishRoll(false);
    this.player?.classList.remove("lexiflow-inline-active");
    this.host?.remove();
    this.host = this.line = this.viewport = this.status = this.player = undefined;
    this.shownHintKeys.clear(); this.reportedPresentationHints.clear(); this.presented.clear();
    this.lastPresentation = { shown: 0, lines: [], hintEntryIds: [] };
  }

  private visualLineCount(line: HTMLElement): number {
    const lineHeight = Number.parseFloat(getComputedStyle(line).lineHeight);
    return lineHeight > 0 ? line.offsetHeight / lineHeight : 0;
  }

  /** Compact the visual layout without changing native offsets, lookup or timestamps. */
  private layoutRows(source: CaptionSource, hints: readonly Hint[]): number[] {
    const { caption } = source;
    const boundaries = [0, ...source.lineBreaks.filter(value => value > 0 && value < caption.length), caption.length];
    const native = boundaries.slice(0, -1).map((start, i) => caption.slice(start, boundaries[i + 1]).trim());
    const line = this.line;
    const prior = this.cueLayout;
    const cut = boundaries[1];
    const sameCue = prior?.text === caption && prior.cut === cut;
    const shared = sameCue ? prior.shared : native.length === 2 && this.nativeRows.length === 2 &&
      this.nativeRows[1] === native[0] && this.nativeRows[0] !== native[0];
    this.nativeRows = native;
    if (!this.capacityHasHistory || !line || native.length !== 2 || source.windowBreaks?.length ||
        shared || native.some(text => /^(?:>>\s*|[-–—]\s+)/u.test(text))) {
      this.cueLayout = { text: caption, cut, width: 0, font: "", shared, compact: false };
      return boundaries;
    }
    if (!line.style.fontSize && this.player) {
      const segment = visibleSegments(this.player)[0];
      if (segment) line.style.fontSize = getComputedStyle(segment).fontSize;
    }
    const style = getComputedStyle(line);
    const font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    const width = this.viewport?.clientWidth ?? 0;
    if (sameCue && prior.width === width && prior.font === font) return prior.compact ? [0, caption.length] : boundaries;
    const meter = this.textWidth ??= document.createElement("canvas").getContext("2d") ?? undefined;
    let compact = false;
    if (meter && width > 0) {
      meter.font = font;
      const padding = (Number.parseFloat(style.fontSize) || 0) * .6 + 2;
      const glyphWidth = (text: string) => meter.measureText(text).width;
      // Only two short sublines are compacted; a long spoken line keeps its original grouping.
      compact = native.every(text => glyphWidth(text) <= width * .4) &&
        glyphWidth(caption) + hints.reduce((sum, hint) => sum + glyphWidth(`(${hint.chineseGloss})`), padding) <= width;
    }
    this.cueLayout = { text: caption, cut, width, font, shared, compact };
    // A late gloss may wrap naturally in .line; it never triggers a fresh caption roll.
    return compact ? [0, caption.length] : boundaries;
  }

  private layoutGeometry(): string {
    return `${this.viewport?.clientWidth ?? 0}:${this.line?.style.fontSize ?? ""}`;
  }

  needsLayout(): boolean {
    return this.capacityHasHistory && !!this.line?.textContent && this.fittedGeometry !== this.layoutGeometry();
  }

  render(view: StreamView, preferences: PreferenceView, source?: CaptionSource): number {
    if (this.gapTimer !== undefined) clearTimeout(this.gapTimer);
    this.gapTimer = undefined;
    const host = this.ensure();
    if (!host || !this.line) { this.lastPresentation = { shown: 0, lines: [], hintEntryIds: [] }; return 0; }
    if (!source) {
      this.singleWindow = false; this.cueLayout = undefined; this.nativeRows = []; this.fittedGeometry = "";
      this.reportedPresentationHints.clear();
      host.dataset.lexiflowState = "idle";
      if (this.status) this.status.textContent = "";
      this.finishRoll(false);
      if (this.line.childNodes.length) this.line.replaceChildren();
      if (this.player?.classList.contains("lexiflow-inline-active")) this.player.classList.remove("lexiflow-inline-active");
      this.lastPresentation = { shown: 0, lines: [], hintEntryIds: [] }; return 0;
    }
    const existing = new Map(Array.from(this.line.querySelectorAll<HTMLElement>(".caption-row:not([data-history]) [data-node-key]"))
      .map(node => [node.dataset.nodeKey!, node]));
    const segments = view.event ? snapshotSegments(view.event.request.currentSnapshot) : [];
    const anchor = (offset: number): string => {
      let base = 0;
      for (const segment of segments) {
        if (offset < base + segment.text.length) return segment.key;
        base += segment.text.length;
      }
      return `text-${offset}`;
    };
    const keys = new Set(segments.map(segment => segment.key));
    this.shownHintKeys = new Set([...this.shownHintKeys].filter(key => [...keys].some(segmentKey => key.startsWith(`${segmentKey}:`))));
    const frozen = new Set(view.frozenKeys ?? []);
    // Offset from the segment's right edge survives prefix trimming, while two
    // occurrences of the same word in a single segment keep distinct identities.
    const occurrencePosition = (offset: number): string => {
      let end = 0;
      for (const segment of segments) {
        end += segment.text.length;
        if (offset < end) return `${segment.key}:${end - offset}`;
      }
      return `text-${offset}:0`;
    };
    const hintKey = (hint: Hint): string => `${occurrencePosition(hint.startOffset)}:${hint.lexiconEntryId}:${hint.lexiconVersion}:${source.caption.slice(hint.startOffset, hint.endOffset)}`;
    const touchesFrozen = (hint: Hint): boolean => {
      let offset = 0;
      for (const segment of segments) {
        if (offset < hint.endOffset && hint.startOffset < offset + segment.text.length && frozen.has(segment.key)) return true;
        offset += segment.text.length;
      }
      return false;
    };
    const matchesSnapshot = !!view.event && source.caption === snapshotText(view.event.request.currentSnapshot);
    const possibleHints = matchesSnapshot ? view.hints ?? [] : [];
    const hints = preferences.ready ? possibleHints.filter(hint => !preferences.hiddenIds.has(hint.lexiconEntryId) &&
      (!touchesFrozen(hint) || this.shownHintKeys.has(hintKey(hint)))) : [];
    const layoutOnly = this.cueLayout?.text === source.caption;
    const boundaries = this.layoutRows(source, hints);
    const geometryChanged = layoutOnly && this.fittedGeometry !== this.layoutGeometry();
    this.fittedGeometry = this.layoutGeometry();
    const desired = Array.from({ length: boundaries.length - 1 }, () => [] as HTMLElement[]);
    const rowTexts = boundaries.slice(0, -1).map((start, index) => source.caption.slice(start, boundaries[index + 1]).trim());
    const actualHintKeys = new Set(hints.map(hintKey));
    const newlyPresentedHints = hints.filter(hint => !this.reportedPresentationHints.has(hintKey(hint)));
    for (const key of [...this.reportedPresentationHints]) if (!actualHintKeys.has(key)) this.reportedPresentationHints.delete(key);
    for (const hint of newlyPresentedHints) this.reportedPresentationHints.add(hintKey(hint));
    host.dataset.lexiflowState = view.state === "ready" && !hints.length ? "no-pending" : view.state;
    if (this.status) {
      this.status.textContent = view.runtimeFailure === "initializing" ? "服务正在初始化，英文字幕保持显示。"
        : view.runtimeFailure === "incompatible" ? "服务/API/资料身份不兼容，英文字幕保持显示。"
        : view.runtimeFailure === "unavailable" ? "服务不可用，英文字幕保持显示。"
        : view.state === "waiting" ? "正在检查服务状态，英文字幕保持显示。"
        : view.runtimeMode === "demo" ? "演示模式 · 非正式就绪。"
        : !preferences.ready && view.event ? preferences.message
        : view.state === "no-pending" || (view.state === "ready" && !hints.length) ? "当前字幕没有可显示的词汇提示。"
        : "";
    }
    const span = (start: number, end: number, className = ""): void => {
      if (start === end) return;
      const breakAt = boundaries.find(value => value > start && value < end);
      if (breakAt !== undefined) { span(start, breakAt, className); span(breakAt, end, className); return; }
      const text = source.caption.slice(start, end);
      const key = `${anchor(start)}:${className}:${text}`;
      const node = existing.get(key) ?? document.createElement("span");
      node.dataset.nodeKey = key;
      node.className = className; node.setAttribute("aria-hidden", "true");
      if (node.textContent !== text) node.textContent = text;
      const row = boundaries.findIndex((value, index) => index < desired.length && start >= value && start < boundaries[index + 1]);
      desired[Math.max(0, row)].push(node);
    };
    const english = (start: number, end: number): void => {
      // 普通英文也按增量片段切分，新增后缀不会替换旧英文节点。
      let cursor = start, base = 0;
      for (const segment of segments) {
        base += segment.text.length;
        if (base > cursor && base < end) { span(cursor, base); cursor = base; }
      }
      span(cursor, end);
    };
    let offset = 0;
    const hintNodes = new Map<Hint, HTMLButtonElement>();
    for (const hint of hints) {
      const term = source.caption.slice(hint.startOffset, hint.endOffset);
      // 单个 hint 覆盖多个空格分隔词才标为词组；不合并相邻提示，也不拆撇号/连字符单词。
      const className = /\S+\s+\S/u.test(term.trim()) ? "hint-term hint-phrase" : "hint-term";
      english(offset, hint.startOffset); span(hint.startOffset, hint.endOffset, className);
      const key = `gloss:${hintKey(hint)}`;
      let button = existing.get(key) as HTMLButtonElement | undefined;
      if (!button) {
        button = document.createElement("button"); button.dataset.nodeKey = key; button.dataset.entryId = hint.lexiconEntryId; button.className = "gloss";
        button.textContent = `(${hint.chineseGloss})`;
        button.title = `不再提示「${term}」及该词条词形，仅本机`;
        button.setAttribute("aria-label", `${term}：${hint.chineseGloss}。不再提示该词条，仅本机`);
        button.addEventListener("click", event => { event.stopPropagation(); this.suppress(hint.lexiconEntryId); });
      }
      const row = boundaries.findIndex((value, index) => index < desired.length && hint.startOffset >= value && hint.startOffset < boundaries[index + 1]);
      desired[Math.max(0, row)].push(button); hintNodes.set(hint, button); offset = hint.endOffset;
      this.shownHintKeys.add(hintKey(hint));
    }
    english(offset, source.caption.length);
    const singleWindow = !source.windowBreaks?.length;
    this.renderRows(desired, boundaries, anchor, rowTexts, layoutOnly, geometryChanged, this.singleWindow && singleWindow, preferences.hiddenIds);
    if (matchesSnapshot && !document.hidden) {
      this.observeVocabulary(keys, possibleHints.map(hint => ({
        entryId: hint.lexiconEntryId, version: hint.lexiconVersion, segmentKey: anchor(hint.startOffset),
        occurrence: hintKey(hint), displayed: this.line!.contains(hintNodes.get(hint) ?? null),
        english: source.caption.slice(hint.startOffset, hint.endOffset), chinese: hint.chineseGloss,
        frequencyRank: hint.frequencyRank
      })));
    }
    this.singleWindow = singleWindow;
    this.position();
    if (!this.player?.classList.contains("lexiflow-inline-active")) this.player?.classList.add("lexiflow-inline-active");
    const lines: RenderedLine[] = [];
    const addRow = (row: HTMLElement): void => {
      const rowKey = row.dataset.rowKey;
      const lineIndex = boundaries.findIndex((value, index) => index < boundaries.length - 1 && anchor(value) === rowKey);
      if (lineIndex < 0) {
        const prior = rowKey ? this.presented.get(rowKey) : undefined;
        if (prior) lines.push(prior as RenderedLine);
        return;
      }
      const start = boundaries[lineIndex], end = boundaries[lineIndex + 1]; let base = 0;
      const rowSegments: string[] = [];
      for (const segment of segments) {
        if (base < end && start < base + segment.text.length) rowSegments.push(segment.key);
        base += segment.text.length;
      }
      if (!rowSegments.length) {
        const prior = rowKey ? this.presented.get(rowKey) : undefined;
        if (prior) lines.push(prior as RenderedLine);
        return;
      }
      const rowHints = hints.filter(hint => hint.startOffset < end && start < hint.endOffset)
        .map(hint => ({ startOffset: hint.startOffset, endOffset: hint.endOffset, chineseGloss: hint.chineseGloss }));
      lines.push({ subtitleKey: rowSegments[0], segmentKeys: rowSegments,
        positionMs: view.event?.videoTimeMs ?? null, text: row.textContent ?? "", start, end,
        caption: source.caption.slice(start, end), hints: rowHints });
    };
    for (const row of Array.from(this.line.querySelectorAll<HTMLElement>(":scope > .caption-row:not([data-history])"))) addRow(row);
    for (const row of Array.from(this.outgoing?.querySelectorAll<HTMLElement>(".caption-row:not([data-history])") ?? [])) addRow(row);
    for (const line of lines) this.presented.set(line.subtitleKey, line);
    while (this.presented.size > 64) this.presented.delete(this.presented.keys().next().value!);
    this.lastPresentation = { shown: hints.length, lines, hintEntryIds: newlyPresentedHints.map(hint => hint.lexiconEntryId) };
    return hints.length;
  }

  takePresentation(): OverlayPresentation { const receipt = this.lastPresentation; this.lastPresentation = { shown: 0, lines: [], hintEntryIds: [] }; return receipt; }

  updateDiagnostics(value: ReturnType<Diagnostics["snapshot"]>): void {
    if (this.host) this.host.dataset.lexiflowDiagnostics = JSON.stringify(value);
  }

  position(): void {
    this.syncMotionRate();
    if (!this.host || !this.line?.textContent || !this.player) return;
    const segments = visibleSegments(this.player);
    if (!segments.length) return;
    const playerBox = this.player.getBoundingClientRect();
    // 滚动文字的底部随 transform 每帧变化；裁剪窗口才是稳定的字幕基线。
    const bottom = Math.max(...segments.map(segment => {
      const window = segment.closest<HTMLElement>(".caption-window");
      return window && clipsCaptionRows(window) ? window.getBoundingClientRect().bottom : segment.getBoundingClientRect().bottom;
    }));
    const nextBottom = `${Math.max(0, Math.round(playerBox.bottom - bottom))}px`;
    const fontSize = getComputedStyle(segments[0]).fontSize;
    if (this.host.style.bottom !== nextBottom) this.host.style.bottom = nextBottom;
    if (this.line.style.fontSize !== fontSize) this.line.style.fontSize = fontSize;
    this.updateHeightLimit();
  }

  private motionSpeed(): number {
    const playbackRate = this.player?.querySelector("video")?.playbackRate ?? 1;
    // 普通及慢放保留动效手感；倍速下还要压缩新行被裁剪遮住的墙钟时间。
    return Number.isFinite(playbackRate) ? Math.max(1, Math.min(4, 2 * playbackRate - 1)) : 1;
  }

  private syncMotionRate(): void {
    if (!this.motionUntil) return;
    const speed = this.motionSpeed();
    if (speed === this.motionScale) return;
    const remaining = this.motionUntil - performance.now();
    if (remaining <= 0) return;
    const ratio = speed / this.motionScale;
    this.motionScale = speed;
    this.timelineRate *= ratio;
    for (const animation of this.rowAnimations) animation.updatePlaybackRate(this.timelineRate);
    this.endMotionAfter(remaining / ratio);
  }

  private endMotionAfter(duration: number): void {
    if (this.motionTimer !== undefined) clearTimeout(this.motionTimer);
    this.motionUntil = performance.now() + duration;
    this.motionTimer = setTimeout(() => this.finishRoll(), duration + 32);
  }

  private cancelAnimations(): void {
    for (const animation of this.rowAnimations) animation.cancel();
    this.rowAnimations = [];
    this.timelineRate = 1;
  }

  private finishRoll(notifyExit = true): void {
    if (this.motionTimer !== undefined) clearTimeout(this.motionTimer);
    this.motionTimer = undefined; this.motionUntil = 0; this.motionScale = 1; this.boundedBatch = false;
    this.cancelAnimations();
    if (notifyExit && this.outgoing) {
      for (const row of Array.from(this.outgoing.querySelectorAll<HTMLElement>(".caption-row"))) {
        const key = row.dataset.rowKey;
        const line = key ? this.presented.get(key) : undefined;
        if (line) { this.presented.delete(line.subtitleKey); try { this.onPresentationExit(line); } catch { /* diagnostics cannot affect rendering */ } }
      }
    }
    this.outgoing?.remove(); this.outgoing = undefined;
    if (this.viewport?.style.minHeight) this.viewport.style.minHeight = "";
  }

  private translate(element: HTMLElement, first: number, last: number, duration: number, easing = "ease-out"): void {
    if (Math.abs(last - first) < .5) return;
    this.rowAnimations.push(element.animate(
      [{ transform: `translateY(${first}px)` }, { transform: `translateY(${last}px)` }],
      { duration, easing, fill: "both" }));
  }

  private translateBatch(element: HTMLElement, from: number, middle: number, to: number,
    timing: { duration: number; enter: number; settle: number; start: number | null }): void {
    const { duration, enter, settle, start } = timing;
    const animation = element.animate([
      { transform: `translateY(${from}px)`, offset: 0, easing: "ease-out" },
      { transform: `translateY(${middle}px)`, offset: enter / duration },
      { transform: `translateY(${middle}px)`, offset: 1 - settle / duration, easing: "ease-out" },
      { transform: `translateY(${to}px)`, offset: 1 }
    ], { duration, fill: "both" });
    if (start !== null) animation.startTime = start;
    this.rowAnimations.push(animation);
  }

  private renderRows(desired: HTMLElement[][], boundaries: number[], anchor: (offset: number) => string, rowTexts: string[], layoutOnly: boolean, geometryChanged: boolean, singleWindow: boolean, suppressed: Set<string>): void {
    const line = this.line, viewport = this.viewport;
    if (!line || !viewport) return;
    const oldRows = Array.from(line.querySelectorAll<HTMLElement>(":scope > .caption-row"));
    const oldActive = oldRows.filter(row => row.dataset.history === undefined);
    const oldByKey = new Map(oldActive.map(row => [row.dataset.rowKey!, row]));
    const used = new Set<HTMLElement>();
    const rows = desired.map((_, index) => {
      const key = anchor(boundaries[index]), text = rowTexts[index];
      const anchored = oldByKey.get(key);
      const exact = anchored && !used.has(anchored) &&
        (!key.startsWith("text-") || this.rowSourceText.get(anchored) === text) ? anchored : undefined;
      // The immediate English render can precede its snapshot identity. Rekey in
      // place on confirmation, but never reuse different text at a provisional offset.
      const provisional = oldActive.find(row => !used.has(row) && this.rowSourceText.get(row) === text &&
        (key.startsWith("text-") || row.dataset.rowKey?.startsWith("text-")));
      const row = exact ?? provisional ?? document.createElement("span");
      row.className = "caption-row"; row.dataset.rowKey = key;
      this.rowSourceText.set(row, text); used.add(row);
      return row;
    });
    const sameRows = rows.length === oldRows.length && rows.every((row, index) => row === oldRows[index]);
    const contents = rows.map(row => row.querySelector<HTMLElement>(":scope > .row-content"));
    const unchanged = contents.map((content, index) => !!content && content.childNodes.length === desired[index].length &&
      desired[index].every((node, offset) => content!.childNodes[offset] === node));
    const patchContents = (): void => {
      rows.forEach((row, index) => {
        if (unchanged[index]) return;
        const content = contents[index] ?? document.createElement("span");
        if (!contents[index]) { content.className = "row-content"; row.append(content); }
        content.replaceChildren(...desired[index]);
      });
    };
    const changedLimit = this.lineLimitChanged; this.lineLimitChanged = false;
    if (this.capacityHasHistory || oldActive.length !== oldRows.length) {
      this.renderHistoryRows(line, viewport, oldRows, oldActive, rows, changedLimit, layoutOnly, geometryChanged, suppressed,
        unchanged.every(Boolean), patchContents);
      return;
    }
    if (sameRows && unchanged.every(Boolean)) return;

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const now = performance.now();
    const continuing = !reducedMotion && sameRows && this.motionUntil > now;
    const shifting = !reducedMotion && oldRows.length === 2 && rows.length === 2 &&
      oldRows[1] === rows[0] && oldRows[0] !== rows[0];
    // 取正在显示的位置，而不是刚撤销动画后的排版位置：回包重排不能让共享行跳帧。
    const previous = new Map(oldRows.map(row => [row, row.getBoundingClientRect().top]));
    const previousHeight = viewport.getBoundingClientRect().height;
    const beforeLines = this.visualLineCount(line);
    const boundedContinuation = continuing && this.boundedBatch;
    if (boundedContinuation) {
      const heights = rows.map(row => row.offsetHeight);
      patchContents();
      // In-place gloss changes must not replay or cut short the held midpoint.
      if (rows.every((row, index) => row.offsetHeight === heights[index])) return;
    }
    // Count physical height as well as DOM rows: a single segment can wrap to two lines.
    const canBatch = !reducedMotion && singleWindow && oldRows.length > 0 && oldRows.length <= 2 &&
      rows.length <= 2 && rows.every(row => !oldRows.includes(row)) &&
      beforeLines >= 1.8;
    const ghostTop = continuing ? this.outgoing?.getBoundingClientRect().top
      : shifting || canBatch ? previous.get(oldRows[0]) : undefined;
    if (continuing) this.cancelAnimations(); else this.finishRoll();
    if (!boundedContinuation) patchContents();
    if (!sameRows) this.setDisplayRows(line, rows);
    const afterLines = this.visualLineCount(line);
    const batch = canBatch && afterLines >= 1.8;
    const staged = batch && beforeLines <= 2.2 && afterLines <= 2.2;
    if (reducedMotion || (!sameRows && !shifting && !batch)) return;
    if (shifting || batch) {
      this.showOutgoing(line, viewport, batch ? oldRows : [oldRows[0]]);
    }
    if (this.outgoing) viewport.style.minHeight = `${previousHeight}px`;
    const clip = viewport.getBoundingClientRect();
    // 词释即使在滚动后才引发换行，画面中原有行也应短暂上移而不是跳到新高度。
    const speed = this.motionSpeed();
    const remaining = this.motionUntil - now;
    const fast = Math.min(1, (speed - 1) / 2);
    const enter = (BATCH_STEP_MS - FAST_BATCH_ENTER_REDUCTION_MS * fast) / speed;
    // Keep the readable handoff while shortening the two moves at higher rates.
    const settle = (BATCH_STEP_MS - FAST_BATCH_TAIL_REDUCTION_MS * fast) / speed;
    const duration = staged ? enter + BATCH_HOLD_MS + settle : boundedContinuation ? Math.max(1, remaining) : continuing ? Math.max(1, remaining,
      ...rows.map(row => Math.abs((previous.get(row) ?? row.getBoundingClientRect().top) - row.getBoundingClientRect().top) / (.46 * speed)))
      : (shifting ? 420 : batch ? 390 : 180) / speed;
    const natural = this.outgoing?.getBoundingClientRect();
    // Move both stacks together. Once the first old line is gone, the old lower
    // line and new upper line share the viewport before the new lower line enters.
    const batchShift = batch && natural && ghostTop !== undefined
      ? Math.max(clip.bottom, ghostTop + natural.height) - line.getBoundingClientRect().top : 0;
    // Pin the held frame to the viewport, not halfway through the total travel:
    // the source or an interrupted animation can change the starting geometry.
    const lineBox = line.getBoundingClientRect();
    const sourceShift = clip.bottom - lineBox.top;
    const middleShift = staged ? sourceShift - lineBox.height / 2 : 0;
    const clock = document.timeline.currentTime;
    const timing = { duration, enter, settle, start: typeof clock === "number" ? clock : null };
    const easing = batch ? "ease-in-out" : shifting || continuing ? "linear" : "ease-out";
    for (const row of rows) {
      const top = row.getBoundingClientRect().top;
      const from = previous.get(row) ?? (shifting ? clip.bottom : top + batchShift);
      if (staged) this.translateBatch(row, sourceShift, middleShift, 0, timing);
      else this.translate(row, from - top, 0, duration, easing);
    }
    if (this.outgoing && natural && ghostTop !== undefined) {
      const exit = Math.min(ghostTop - batchShift, clip.top - natural.height);
      if (staged) this.translateBatch(this.outgoing, ghostTop - natural.top, clip.top - natural.height / 2 - natural.top, exit - natural.top, timing);
      else this.translate(this.outgoing, ghostTop - natural.top, exit - natural.top, duration, easing);
    }
    // A late gloss can change the destination by a full visual line. Rebase from
    // the painted position and cap its speed instead of squeezing it into the last frames.
    if ((!continuing || duration > remaining + 1) && (this.rowAnimations.length || this.outgoing)) {
      this.motionScale = speed;
      this.boundedBatch = staged;
      this.endMotionAfter(duration);
    }
  }

  private setDisplayRows(line: HTMLElement, rows: HTMLElement[]): void {
    const children: Node[] = [];
    rows.forEach((row, index) => {
      if (index) { const br = document.createElement("br"); br.className = "row-break"; children.push(br); }
      children.push(row);
    });
    line.replaceChildren(...children);
  }

  private showOutgoing(line: HTMLElement, viewport: HTMLElement, rows: HTMLElement[]): void {
    const ghost = document.createElement("div"); ghost.className = "outgoing"; ghost.append(...rows);
    ghost.style.fontSize = line.style.fontSize; ghost.inert = true; ghost.setAttribute("aria-hidden", "true");
    viewport.append(ghost); this.outgoing = ghost;
  }

  private renderHistoryRows(line: HTMLElement, viewport: HTMLElement, oldRows: HTMLElement[], oldActive: HTMLElement[],
    rows: HTMLElement[], changedLimit: boolean, layoutOnly: boolean, geometryChanged: boolean, suppressed: Set<string>,
    contentsUnchanged: boolean, patchContents: () => void): void {
    const addsRow = rows.some(row => !oldActive.includes(row));
    const sameActive = rows.length === oldActive.length && rows.every((row, index) => row === oldActive[index]);
    const keepsHistory = this.capacityHasHistory;
    const history = keepsHistory ? oldRows.filter(row => row.dataset.history !== undefined) : [];
    // A roll retires only the prefix before a still-active row, or the whole old cue
    // when the native player replaces its full caption. Layout-only updates do not retire cues.
    if (keepsHistory && addsRow && !changedLimit && !layoutOnly) {
      const shared = oldActive.findIndex(row => rows.includes(row));
      history.push(...oldActive.slice(0, shared === -1 ? oldActive.length : shared)
        .filter(row => !!row.dataset.rowKey && !row.dataset.rowKey.startsWith("text-")));
    }
    const activeKeys = new Set(rows.map(row => row.dataset.rowKey));
    const eligible = history.filter(row => !activeKeys.has(row.dataset.rowKey));
    const hiddenGloss = eligible.flatMap(row => Array.from(row.querySelectorAll<HTMLElement>(".gloss[data-entry-id]")))
      .filter(gloss => suppressed.has(gloss.dataset.entryId!));
    if (sameActive && contentsUnchanged && !changedLimit && !geometryChanged && !hiddenGloss.length &&
        oldRows.length === eligible.length + rows.length) return;

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const now = performance.now();
    const continuing = !reducedMotion && !changedLimit && sameActive && this.motionUntil > now;
    const previous = new Map(oldRows.map(row => [row, row.getBoundingClientRect().top]));
    const oldHeight = viewport.getBoundingClientRect().height;
    const ghostTop = continuing ? this.outgoing?.getBoundingClientRect().top : previous.get(oldRows[0]);
    const canPatchInPlace = continuing && !geometryChanged && !hiddenGloss.length;
    if (canPatchInPlace) {
      const heights = rows.map(row => row.offsetHeight);
      patchContents();
      if (rows.every((row, index) => row.offsetHeight === heights[index])) return;
    }
    if (continuing) this.cancelAnimations(); else this.finishRoll();

    if (!canPatchInPlace) patchContents();
    for (const gloss of hiddenGloss) gloss.remove();
    if (!sameActive) this.setDisplayRows(line, [...eligible, ...rows]);
    const weight = (row: HTMLElement) => Math.max(1, Math.round(this.visualLineCount(row)));
    const free = this.lineLimit - rows.reduce((sum, row) => sum + weight(row), 0);
    const retained = fitCaptionHistory(eligible, free, weight);
    for (const row of retained) {
      row.dataset.history = ""; row.inert = true; row.setAttribute("aria-hidden", "true");
    }
    const display = [...retained, ...rows];
    if (!sameActive || display.length !== oldRows.length) this.setDisplayRows(line, display);
    if (reducedMotion || changedLimit || geometryChanged) return;

    const advancing = !layoutOnly && !sameActive && addsRow && oldRows.length > 0;
    if (advancing) {
      const firstRetained = oldRows.findIndex(row => display.includes(row));
      const departing = oldRows.slice(0, firstRetained === -1 ? oldRows.length : firstRetained);
      if (departing.length) this.showOutgoing(line, viewport, departing);
    }
    if (this.outgoing) viewport.style.minHeight = `${oldHeight}px`;
    const clip = viewport.getBoundingClientRect();
    const firstNew = advancing ? display.find(row => !previous.has(row)) : undefined;
    const incomingShift = firstNew ? Math.max(0, clip.bottom - firstNew.getBoundingClientRect().top) : 0;
    const speed = this.motionSpeed();
    const duration = continuing ? Math.max(1, this.motionUntil - now) : (advancing ? (display.filter(row => !previous.has(row)).length > 1 ? 330 : 260) : 180) / speed;
    const easing = advancing ? "linear" : "ease-out";
    for (const row of display) {
      const top = row.getBoundingClientRect().top;
      this.translate(row, (previous.get(row) ?? top + incomingShift) - top, 0, duration, easing);
    }
    if (this.outgoing && ghostTop !== undefined) {
      const natural = this.outgoing.getBoundingClientRect();
      const exit = Math.min(ghostTop - incomingShift, clip.top - natural.height);
      this.translate(this.outgoing, ghostTop - natural.top, exit - natural.top, duration, easing);
    }
    if (!continuing && (this.rowAnimations.length || this.outgoing)) {
      this.motionScale = speed; this.endMotionAfter(duration);
    }
  }

  private ensure(): HTMLElement | undefined {
    const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
    if (this.host?.isConnected && this.player === player) return this.host;
    this.finishRoll(false); this.singleWindow = false; this.cueLayout = undefined; this.nativeRows = []; this.fittedGeometry = "";
    this.shownHintKeys.clear(); this.presented.clear();
    if (this.player?.classList.contains("lexiflow-inline-active")) this.player.classList.remove("lexiflow-inline-active");
    this.host?.remove();
    this.line = undefined; this.viewport = undefined; this.status = undefined;
    if (!player?.querySelector("#ytp-caption-window-container")) return undefined;
    this.player = player;
    const host = document.createElement("div");
    host.id = OVERLAY_ID;
    host.dataset.lexiflowState = "idle";
    host.style.cssText = "position:absolute;left:5%;right:5%;bottom:12%;z-index:2147483646;pointer-events:none;text-align:center";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = ":host{font-family:Arial,sans-serif;white-space:normal}.viewport{position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end}.line{display:block;flex:none;white-space:normal;color:white;font-weight:500;line-height:1.55;text-shadow:0 1px 2px #000;overflow-wrap:anywhere}.line:empty{display:none}.service-status:empty{display:none}.service-status{display:block;position:absolute;top:100%;left:0;right:0;margin-top:.25em;padding:.1em .3em;border-radius:.2em;background:rgba(0,0,0,.72);color:#eee;font-size:.42em;line-height:1.35;text-shadow:none}.caption-row{display:block;text-align:center}.row-content{padding:.12em .25em;background:rgba(0,0,0,.8);box-decoration-break:clone;-webkit-box-decoration-break:clone}.row-break{display:none}.outgoing{position:absolute;top:0;left:0;right:0;pointer-events:none;color:white;font:inherit;line-height:1.55;text-shadow:0 1px 2px #000;overflow-wrap:anywhere}.outgoing .gloss{pointer-events:none}.hint-phrase{text-decoration-line:underline;text-decoration-color:#ffe58f;text-decoration-thickness:.08em;text-underline-offset:.14em;text-decoration-skip-ink:none}.gloss{display:inline;white-space:normal;color:#ffe58f;background:none;border:0;padding:0;font:inherit;text-shadow:inherit;cursor:pointer;pointer-events:auto;max-width:100%;overflow-wrap:anywhere}.gloss:focus-visible{outline:2px solid #ffe58f}";
    this.line = document.createElement("span");
    this.line.className = "line";
    this.viewport = document.createElement("div"); this.viewport.className = "viewport";
    this.viewport.append(this.line);
    this.status = document.createElement("span"); this.status.className = "service-status"; this.status.setAttribute("role", "status"); this.status.setAttribute("aria-live", "polite");
    root.append(style, this.viewport, this.status);
    player.append(host);
    this.host = host;
    return host;
  }
}
