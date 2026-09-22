import {
  MAX_CAPTION_LENGTH,
  type ApiResult,
  type CaptionHintRequest
} from "./protocol";
import {
  CaptionStreamCoordinator,
  type CaptionEvent,
  type StreamView
} from "./stream";

const captionSelector = ".ytp-caption-segment";
const overlayId = "lexiflow-caption-overlay";

function normalizeCaption(value: string): string {
  return value.normalize("NFC").replace(/\s+/g, " ").trim();
}

function videoIdFromLocation(): string | undefined {
  const value = new URL(location.href).searchParams.get("v")?.trim();
  return value && value.length <= 256 ? value : undefined;
}

function bytesToHex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256(value: string): Promise<string> {
  return bytesToHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

async function contentId(videoId: string): Promise<string> {
  const digest = await sha256(`youtube\u0000${videoId}`);
  return `${digest.slice(0, 8)}-${digest.slice(8, 12)}-5${digest.slice(13, 16)}-8${digest.slice(17, 20)}-${digest.slice(20, 32)}`;
}

class BilingualOverlay {
  private host?: HTMLElement;
  private hint?: HTMLSpanElement;

  render(view: StreamView): void {
    const host = this.ensure();
    if (host === undefined) return;
    if (host.dataset.lexiflowState !== view.state) host.dataset.lexiflowState = view.state;
    if (this.hint === undefined) return;
    const text = view.state === "ready" && view.gloss ? `（${view.gloss}）` : "";
    if (this.hint.textContent !== text) this.hint.textContent = text;
    this.hint.setAttribute(
      "aria-label",
      view.state === "ready" && view.gloss ? `LexiFlow 中文提示：${view.gloss}` : ""
    );
    this.position();
  }

  position(): void {
    if (this.host === undefined || this.hint === undefined || !this.hint.textContent) return;
    const player = this.host.parentElement;
    if (player === null) return;
    const segments = Array.from(player.querySelectorAll<HTMLElement>(captionSelector))
      .filter((segment) => segment.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }));
    if (segments.length === 0) return;
    const playerBox = player.getBoundingClientRect();
    const top = Math.min(...segments.map((segment) => segment.getBoundingClientRect().top)) - playerBox.top;
    const bottom = Math.max(...segments.map((segment) => segment.getBoundingClientRect().bottom)) - playerBox.top;
    const height = this.hint.getBoundingClientRect().height;
    // Prefer below English, but move above it when controls leave insufficient space.
    const y = bottom + 8 + height <= playerBox.height - 52 ? bottom + 8 : Math.max(8, top - height - 8);
    const nextTop = `${Math.round(y)}px`;
    if (this.host.style.top !== nextTop) this.host.style.top = nextTop;
    if (this.host.style.bottom !== "auto") this.host.style.bottom = "auto";
  }

  private ensure(): HTMLElement | undefined {
    const existing = document.getElementById(overlayId) as HTMLElement | null;
    if (existing !== null) {
      this.host = existing;
      this.hint = existing.shadowRoot?.querySelector("span") ?? undefined;
      return existing;
    }
    const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
    if (player === null) return undefined;
    const host = document.createElement("div");
    host.id = overlayId;
    host.dataset.lexiflowState = "idle";
    host.setAttribute("aria-live", "polite");
    host.style.cssText =
      "position:absolute;left:10%;right:10%;bottom:12%;z-index:2147483647;pointer-events:none;text-align:center";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent =
      ":host{font-family:Arial,sans-serif}span{display:inline-block;max-width:100%;padding:0.16em 0.42em;border-radius:0.22em;background:rgba(0,0,0,.72);color:#ffe58f;font-size:clamp(14px,2vw,24px);font-weight:600;line-height:1.35;text-shadow:0 1px 2px #000}span:empty{display:none}";
    const hint = document.createElement("span");
    root.append(style, hint);
    player.append(host);
    this.host = host;
    this.hint = hint;
    return host;
  }
}

const overlay = new BilingualOverlay();
const coordinator = new CaptionStreamCoordinator(
  (event, requestId) => ({
    promise: chrome.runtime
      .sendMessage({ type: "caption-hints", requestId, payload: event.request })
      .catch(() => ({ ok: false, reason: "network" } as ApiResult)),
    cancel: () => {
      void chrome.runtime.sendMessage({ type: "cancel-caption-hint", requestId }).catch(() => undefined);
    }
  }),
  (view) => overlay.render(view)
);

let sourceSequence = 0;
let sourceRevision = 0;
let activeVideoId: string | undefined;
let activeCaptionKey: string | undefined;
let scheduledCapture: number | undefined;

function currentCaption(): string | undefined {
  const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
  if (player === null || player.classList.contains("ad-showing")) return undefined;
  const text = Array.from(player.querySelectorAll<HTMLElement>(captionSelector))
    .filter((segment) => segment.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }))
    .map((segment) => segment.innerText || segment.textContent || "")
    .map(normalizeCaption)
    .filter(Boolean)
    .join(" ");
  return text || undefined;
}

function scheduleCapture(): void {
  // Bound latency even while unrelated page mutations arrive continuously.
  if (scheduledCapture !== undefined) return;
  scheduledCapture = window.setTimeout(() => {
    scheduledCapture = undefined;
    void captureCurrentCaption();
  }, 50);
}

async function captureCurrentCaption(): Promise<void> {
  overlay.position();
  const video = document.querySelector<HTMLVideoElement>("video");
  const videoId = videoIdFromLocation();
  const caption = currentCaption();
  if (video === null || videoId === undefined || caption === undefined) {
    activeCaptionKey = undefined;
    coordinator.clear(++sourceSequence);
    return;
  }
  if (activeVideoId !== videoId) {
    activeVideoId = videoId;
    sourceRevision += 1;
    activeCaptionKey = undefined;
  }
  const videoTimeMs = Math.max(0, Math.floor((Number.isFinite(video.currentTime) ? video.currentTime : 0) * 1_000));
  const captionKey = `${videoId}\u0000${sourceRevision}\u0000${caption}`;
  if (captionKey === activeCaptionKey) return;
  activeCaptionKey = captionKey;
  const sequence = ++sourceSequence;
  if (caption.length > MAX_CAPTION_LENGTH) {
    coordinator.clear(sequence);
    return;
  }
  const id = await contentId(videoId);
  const segmentId = await sha256(`${id}\u0000${sourceRevision}\u0000${sequence}\u0000${videoTimeMs}\u0000${caption}`);
  if (sequence !== sourceSequence) return;
  const request: CaptionHintRequest = {
    contentId: id,
    contentRevision: sourceRevision,
    segmentId,
    caption,
    startOffset: 0,
    endOffset: caption.length
  };
  const event: CaptionEvent = { key: captionKey, sequence, videoTimeMs, request };
  coordinator.submit(event);
}

scheduleCapture();
new MutationObserver(scheduleCapture).observe(document.documentElement, {
  childList: true,
  subtree: true,
  characterData: true,
  attributes: true,
  attributeFilter: ["class", "style", "hidden", "aria-hidden"]
});
function resetForNavigation(): void {
  activeVideoId = undefined;
  activeCaptionKey = undefined;
  coordinator.clear(++sourceSequence);
  scheduleCapture();
}

document.addEventListener("yt-navigate-start", resetForNavigation);
document.addEventListener("yt-navigate-finish", scheduleCapture);
window.addEventListener("popstate", resetForNavigation);
window.addEventListener("resize", scheduleCapture);
document.addEventListener("timeupdate", scheduleCapture, true);
