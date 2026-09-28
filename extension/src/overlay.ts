import { suppressionKey } from "./preferences";
import { snapshotText, snapshotSegments, type Hint } from "./protocol";
import type { StreamView } from "./stream";
import type { Diagnostics } from "./diagnostics";
import { clipsCaptionRows, type CaptionSource } from "./caption-source";

export const OVERLAY_ID = "lexiflow-caption-overlay";
export const captionSelector = ".ytp-caption-segment";
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
export type PreferenceView = { ready: boolean; entryKeys: Set<string>; message: string };

/** Rendering owns no network or storage; only explicit button activation changes preferences. */
export class BilingualOverlay {
  private host?: HTMLElement;
  private line?: HTMLSpanElement;
  private viewport?: HTMLElement;
  private outgoing?: HTMLElement;
  private rollFrame?: number;
  private rollTimer?: ReturnType<typeof setTimeout>;
  private player?: HTMLElement;
  private shownHintKeys = new Set<string>();

  constructor(private readonly suppress: (entryId: string, lexiconVersion: number) => void) {}

  render(view: StreamView, preferences: PreferenceView, source?: CaptionSource): number {
    const host = this.ensure();
    if (!host || !this.line) return 0;
    if (!source) {
      host.dataset.lexiflowState = "idle";
      this.finishRoll();
      if (this.line.childNodes.length) this.line.replaceChildren();
      if (this.player?.classList.contains("lexiflow-inline-active")) this.player.classList.remove("lexiflow-inline-active");
      return 0;
    }
    const existing = new Map(Array.from(this.line.querySelectorAll<HTMLElement>("[data-node-key]"))
      .map(node => [node.dataset.nodeKey!, node]));
    const boundaries = [0, ...source.lineBreaks.filter(value => value > 0 && value < source.caption.length), source.caption.length];
    const desired = Array.from({ length: boundaries.length - 1 }, () => [] as HTMLElement[]);
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
    const hintKey = (hint: Hint): string => `${anchor(hint.startOffset)}:${hint.lexiconEntryId}:${hint.lexiconVersion}:${hint.senseId}:${source.caption.slice(hint.startOffset, hint.endOffset)}`;
    const touchesFrozen = (hint: Hint): boolean => {
      let offset = 0;
      for (const segment of segments) {
        if (offset < hint.endOffset && hint.startOffset < offset + segment.text.length && frozen.has(segment.key)) return true;
        offset += segment.text.length;
      }
      return false;
    };
    const hints = preferences.ready && view.event && source.caption === snapshotText(view.event.request.currentSnapshot)
      ? view.hints?.filter(hint => !preferences.entryKeys.has(suppressionKey(hint.lexiconEntryId, hint.lexiconVersion)) &&
        (!touchesFrozen(hint) || this.shownHintKeys.has(hintKey(hint)))) ?? [] : [];
    host.dataset.lexiflowState = view.state === "ready" && !hints.length ? "no-pending" : view.state;
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
    for (const hint of hints) {
      const term = source.caption.slice(hint.startOffset, hint.endOffset);
      // 单个 hint 覆盖多个空格分隔词才标为词组；不合并相邻提示，也不拆撇号/连字符单词。
      const className = /\S+\s+\S/u.test(term.trim()) ? "hint-term hint-phrase" : "hint-term";
      english(offset, hint.startOffset); span(hint.startOffset, hint.endOffset, className);
      const key = `${anchor(hint.startOffset)}:gloss:${hint.lexiconEntryId}:${hint.lexiconVersion}:${hint.senseId}:${term}`;
      let button = existing.get(key) as HTMLButtonElement | undefined;
      if (!button) {
        button = document.createElement("button"); button.dataset.nodeKey = key; button.className = "gloss";
        button.textContent = `(${hint.chineseGloss})`;
        button.title = `不再提示「${term}」及该词条词形，仅本机、当前词库版本`;
        button.setAttribute("aria-label", `${term}：${hint.chineseGloss}。不再提示该词条，仅本机、当前词库版本`);
        button.addEventListener("click", event => { event.stopPropagation(); this.suppress(hint.lexiconEntryId, hint.lexiconVersion); });
      }
      const row = boundaries.findIndex((value, index) => index < desired.length && hint.startOffset >= value && hint.startOffset < boundaries[index + 1]);
      desired[Math.max(0, row)].push(button); offset = hint.endOffset;
      this.shownHintKeys.add(hintKey(hint));
    }
    english(offset, source.caption.length);
    this.renderRows(desired, boundaries, anchor);
    this.position();
    if (!this.player?.classList.contains("lexiflow-inline-active")) this.player?.classList.add("lexiflow-inline-active");
    return hints.length;
  }

  updateDiagnostics(value: ReturnType<Diagnostics["snapshot"]>): void {
    if (this.host) this.host.dataset.lexiflowDiagnostics = JSON.stringify(value);
  }

  position(): void {
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
  }

  private finishRoll(): void {
    if (this.rollFrame !== undefined) cancelAnimationFrame(this.rollFrame);
    if (this.rollTimer !== undefined) clearTimeout(this.rollTimer);
    this.rollFrame = undefined; this.rollTimer = undefined;
    if (this.line?.classList.contains("rolling")) this.line.classList.remove("rolling");
    if (this.line?.style.transform) this.line.style.transform = "";
    this.outgoing?.remove(); this.outgoing = undefined;
    if (this.viewport?.style.height) this.viewport.style.height = "";
  }

  private renderRows(desired: HTMLElement[][], boundaries: number[], anchor: (offset: number) => string): void {
    if (!this.line || !this.viewport) return;
    const nextKeys = desired.map((_, index) => anchor(boundaries[index]));
    const currentKeys = Array.from(this.line.querySelectorAll<HTMLElement>(":scope > .caption-row"))
      .map(row => row.dataset.rowKey);
    if (this.outgoing && JSON.stringify(currentKeys) !== JSON.stringify(nextKeys)) this.finishRoll();
    const oldRows = Array.from(this.line.querySelectorAll<HTMLElement>(":scope > .caption-row"));
    const oldByKey = new Map(oldRows.map(row => [row.dataset.rowKey!, row]));
    const rows = desired.map((nodes, index) => {
      const key = anchor(boundaries[index]);
      const row = oldByKey.get(key) ?? document.createElement("span");
      row.className = "caption-row"; row.dataset.rowKey = key;
      let content = row.querySelector<HTMLElement>(":scope > .row-content");
      if (!content) { content = document.createElement("span"); content.className = "row-content"; row.append(content); }
      content.replaceChildren(...nodes);
      return row;
    });
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const roll = !reducedMotion && !this.outgoing && oldRows.length === 2 && rows.length === 2 && oldRows[1] === rows[0] && oldRows[0] !== rows[0];
    const outgoing = roll ? oldRows[0] : undefined;
    const outgoingHeight = outgoing?.getBoundingClientRect().height ?? 0;
    const children: Node[] = [];
    rows.forEach((row, index) => {
      if (index) { const br = document.createElement("br"); br.className = "row-break"; children.push(br); }
      children.push(row);
    });
    this.line.replaceChildren(...children);
    if (outgoing && outgoingHeight > 0) {
      const ghost = document.createElement("div"); ghost.className = "outgoing"; ghost.append(outgoing);
      ghost.style.fontSize = this.line.style.fontSize;
      this.viewport.append(ghost); this.outgoing = ghost;
      this.viewport.style.height = `${this.line.getBoundingClientRect().height}px`;
      this.line.style.transform = `translateY(${outgoingHeight}px)`;
      this.rollFrame = requestAnimationFrame(() => {
        this.rollFrame = undefined;
        if (this.outgoing !== ghost || !this.line) return;
        this.line.classList.add("rolling");
        ghost.classList.add("rolling");
        this.line.style.transform = "translateY(0)";
        ghost.style.transform = `translateY(-${outgoingHeight}px)`;
        this.rollTimer = setTimeout(() => this.finishRoll(), 450);
      });
    }
  }

  private ensure(): HTMLElement | undefined {
    const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
    if (this.host?.isConnected && this.player === player) return this.host;
    this.finishRoll(); this.shownHintKeys.clear();
    if (this.player?.classList.contains("lexiflow-inline-active")) this.player.classList.remove("lexiflow-inline-active");
    this.host?.remove();
    this.line = undefined; this.viewport = undefined;
    if (!player?.querySelector("#ytp-caption-window-container")) return undefined;
    this.player = player;
    const host = document.createElement("div");
    host.id = OVERLAY_ID;
    host.dataset.lexiflowState = "idle";
    host.style.cssText = "position:absolute;left:5%;right:5%;bottom:12%;z-index:2147483646;pointer-events:none;text-align:center";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = ":host{font-family:Arial,sans-serif;white-space:normal}.viewport{position:relative;overflow:hidden}.line{display:block;white-space:normal;color:white;font-weight:500;line-height:1.55;text-shadow:0 1px 2px #000;overflow-wrap:anywhere}.line:empty{display:none}.caption-row{display:block;text-align:center}.row-content{padding:.12em .25em;background:rgba(0,0,0,.8);box-decoration-break:clone;-webkit-box-decoration-break:clone}.row-break{display:none}.outgoing{position:absolute;top:0;left:0;right:0;color:white;font:inherit;line-height:1.55;text-shadow:0 1px 2px #000}.rolling{transition:transform .42s ease-out}.hint-phrase{text-decoration-line:underline;text-decoration-color:#ffe58f;text-decoration-thickness:.08em;text-underline-offset:.14em;text-decoration-skip-ink:none}.gloss{display:inline;white-space:normal;color:#ffe58f;background:none;border:0;padding:0;font:inherit;text-shadow:inherit;cursor:pointer;pointer-events:auto;max-width:100%;overflow-wrap:anywhere}.gloss:focus-visible{outline:2px solid #ffe58f}@media(prefers-reduced-motion:reduce){.rolling{transition:none!important}}";
    this.line = document.createElement("span");
    this.line.className = "line";
    this.viewport = document.createElement("div"); this.viewport.className = "viewport";
    this.viewport.append(this.line);
    root.append(style, this.viewport);
    player.append(host);
    this.host = host;
    return host;
  }
}
