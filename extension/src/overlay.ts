import { suppressionKey } from "./preferences";
import { snapshotText, snapshotSegments, type Hint } from "./protocol";
import type { StreamView } from "./stream";
import type { Diagnostics } from "./diagnostics";
import type { CaptionSource } from "./caption-source";

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
  private player?: HTMLElement;
  private controls?: HTMLElement;
  private details?: HTMLDetailsElement;
  private report?: HTMLPreElement;
  private preferenceMessage?: HTMLElement;
  private latestDiagnostics = "";

  constructor(private readonly suppress: (entryId: string, lexiconVersion: number) => void,
    private readonly restore: () => void, private readonly resetDiagnostics: () => void) {}

  render(view: StreamView, preferences: PreferenceView, source?: CaptionSource): number {
    const host = this.ensure();
    if (!host || !this.line) return 0;
    const hints = preferences.ready && source && view.event && source.caption === snapshotText(view.event.request.currentSnapshot)
      ? view.hints?.filter(hint => !preferences.entryKeys.has(suppressionKey(hint.lexiconEntryId, hint.lexiconVersion))) ?? [] : [];
    host.dataset.lexiflowState = view.state === "ready" && !hints.length ? "no-pending" : view.state;
    if (this.preferenceMessage) this.preferenceMessage.textContent = preferences.message ||
      `仅本机：已抑制 ${preferences.entryKeys.size} 个词条。点击中文即可不再提示。`;
    if (!source) {
      if (this.line.childNodes.length) this.line.replaceChildren();
      this.player?.classList.remove("lexiflow-inline-active");
      return 0;
    }
    const existing = new Map(Array.from(this.line.children).map(node => [(node as HTMLElement).dataset.nodeKey!, node as HTMLElement]));
    const desired: HTMLElement[] = [];
    const segments = view.event ? snapshotSegments(view.event.request.currentSnapshot) : [];
    const anchor = (offset: number): string => {
      let base = 0;
      for (const segment of segments) {
        if (offset < base + segment.text.length) return segment.key;
        base += segment.text.length;
      }
      return `text-${offset}`;
    };
    const span = (start: number, end: number, className = ""): void => {
      if (start === end) return;
      const text = source.caption.slice(start, end);
      const key = `${anchor(start)}:${className}:${text}`;
      const node = existing.get(key) ?? document.createElement("span");
      node.dataset.nodeKey = key;
      node.className = className; node.setAttribute("aria-hidden", "true");
      const breaks = source.lineBreaks.filter(offset => offset >= start && offset < end).map(offset => offset - start);
      const signature = JSON.stringify([text, breaks]);
      if (node.dataset.content !== signature) {
        node.dataset.content = signature;
        const pieces: Node[] = []; let cursor = 0;
        for (const offset of breaks) { pieces.push(document.createTextNode(text.slice(cursor, offset)), document.createElement("br")); cursor = offset; }
        pieces.push(document.createTextNode(text.slice(cursor))); node.replaceChildren(...pieces);
      }
      desired.push(node);
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
      english(offset, hint.startOffset); span(hint.startOffset, hint.endOffset, "hint-term");
      const term = source.caption.slice(hint.startOffset, hint.endOffset);
      const key = `${anchor(hint.startOffset)}:gloss:${hint.lexiconEntryId}:${hint.lexiconVersion}:${hint.senseId}:${term}`;
      let button = existing.get(key) as HTMLButtonElement | undefined;
      if (!button) {
        button = document.createElement("button"); button.dataset.nodeKey = key; button.className = "gloss";
        button.textContent = `(${hint.chineseGloss})`;
        button.title = `不再提示「${term}」及该词条词形，仅本机、当前词库版本`;
        button.setAttribute("aria-label", `${term}：${hint.chineseGloss}。不再提示该词条，仅本机、当前词库版本`);
        button.addEventListener("click", event => { event.stopPropagation(); this.suppress(hint.lexiconEntryId, hint.lexiconVersion); });
      }
      desired.push(button); offset = hint.endOffset;
    }
    english(offset, source.caption.length);
    const retained = new Set(desired);
    for (const node of Array.from(this.line.children)) if (!retained.has(node as HTMLElement)) node.remove();
    let cursor: ChildNode | null = this.line.firstChild;
    for (const node of desired) {
      if (node === cursor) cursor = cursor.nextSibling;
      else this.line.insertBefore(node, cursor);
    }
    this.position();
    if (!this.player?.classList.contains("lexiflow-inline-active")) this.player?.classList.add("lexiflow-inline-active");
    return hints.length;
  }

  updateDiagnostics(value: ReturnType<Diagnostics["snapshot"]>): void {
    this.latestDiagnostics = JSON.stringify(value, null, 2);
    if (this.host) this.host.dataset.lexiflowDiagnostics = JSON.stringify(value);
    if (this.details?.open && this.report) this.report.textContent = this.latestDiagnostics;
  }

  position(): void {
    if (!this.host || !this.line?.textContent || !this.player) return;
    const segments = visibleSegments(this.player);
    if (!segments.length) return;
    const playerBox = this.player.getBoundingClientRect();
    const bottom = Math.max(...segments.map(segment => segment.getBoundingClientRect().bottom));
    const nextBottom = `${Math.max(0, Math.round(playerBox.bottom - bottom))}px`;
    const fontSize = getComputedStyle(segments[0]).fontSize;
    if (this.host.style.bottom !== nextBottom) this.host.style.bottom = nextBottom;
    if (this.line.style.fontSize !== fontSize) this.line.style.fontSize = fontSize;
  }

  private ensure(): HTMLElement | undefined {
    const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
    if (this.host?.isConnected && this.player === player) return this.host;
    this.player?.classList.remove("lexiflow-inline-active");
    this.host?.remove(); this.controls?.remove();
    if (!player?.querySelector("#ytp-caption-window-container")) return undefined;
    this.player = player;
    const host = document.createElement("div");
    host.id = OVERLAY_ID;
    host.dataset.lexiflowState = "idle";
    host.style.cssText = "position:absolute;left:5%;right:5%;bottom:12%;z-index:2147483646;pointer-events:none;text-align:center";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = ":host{font-family:Arial,sans-serif;white-space:normal}.line{display:inline;white-space:normal;box-decoration-break:clone;-webkit-box-decoration-break:clone;padding:.12em .25em;background:rgba(0,0,0,.8);color:white;font-weight:500;line-height:1.3;text-shadow:0 1px 2px #000;overflow-wrap:anywhere}.line:empty{display:none}.hint-term{border-bottom:.09em solid #ffe58f;padding-bottom:.12em;box-decoration-break:clone;-webkit-box-decoration-break:clone}.gloss{display:inline;white-space:normal;color:#ffe58f;background:none;border:0;padding:0;font:inherit;text-shadow:inherit;cursor:pointer;pointer-events:auto;max-width:100%;overflow-wrap:anywhere}.gloss:focus-visible{outline:2px solid #ffe58f}";
    this.line = document.createElement("span");
    this.line.className = "line";
    root.append(style, this.line);
    player.append(host);
    this.host = host;
    this.createControls(player);
    return host;
  }

  private createControls(player: HTMLElement): void {
    const controls = document.createElement("div");
    controls.id = "lexiflow-controls";
    controls.style.cssText = "position:absolute;top:8px;left:8px;z-index:2147483647;font:12px Arial,sans-serif;color:white";
    const root = controls.attachShadow({mode:"open"});
    const style = document.createElement("style");
    style.textContent = "details{background:rgba(0,0,0,.88);border-radius:5px;padding:5px;max-width:min(420px,75vw)}summary{cursor:pointer;list-style:none;color:#ffe58f}button{margin:4px;padding:5px;color:white;background:#333;border:1px solid #888;border-radius:3px;cursor:pointer}p{line-height:1.5;margin:8px 4px}pre{max-height:200px;overflow:auto;font:11px monospace;white-space:pre-wrap}button:focus-visible,summary:focus-visible{outline:2px solid #ffe58f}";
    this.details = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = "LF";
    summary.setAttribute("aria-label", "LexiFlow 设置与诊断");
    this.preferenceMessage = document.createElement("p");
    const restore = document.createElement("button"); restore.textContent = "恢复全部提示";
    restore.addEventListener("click", () => this.restore());
    const reset = document.createElement("button"); reset.textContent = "清空本机统计";
    reset.addEventListener("click", () => this.resetDiagnostics());
    this.report = document.createElement("pre");
    this.details.addEventListener("toggle", () => { if (this.details?.open && this.report) this.report.textContent = this.latestDiagnostics; });
    this.details.append(summary, this.preferenceMessage, restore, reset, this.report);
    root.append(style, this.details);
    root.addEventListener("click", event => event.stopPropagation());
    root.addEventListener("keydown", event => event.stopPropagation());
    player.append(controls); this.controls = controls;
  }
}
