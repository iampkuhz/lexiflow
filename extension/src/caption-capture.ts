import { CaptionViewport, readCaptionSource, type CaptionSource } from "./caption-source";
import { CaptionSnapshotTracker } from "./caption-snapshot";
import { YoutubeSourceMetadata } from "./youtube-source";
import { MAX_CAPTION_LENGTH, type CaptionHintRequest } from "./protocol";
import type { CaptionEvent, CaptionStreamCoordinator } from "./stream";
import type { PageLifecycle } from "./page-lifecycle";
import { OVERLAY_ID } from "./overlay";

export type CaptureDependencies = {
  sha256(value: string): Promise<string>;
  now(): number;
  onWaiting(): void;
  onSource(source?: CaptionSource): void;
  onObserved(): void;
  onOversized(): void;
  onAcquisition(elapsedMs: number): void;
};
/** DOM/viewport/source metadata/snapshot/topic are a single acquisition owner; stream remains request-state owner. */
export class CaptionCapture {
  private readonly viewport = new CaptionViewport();
  private readonly snapshots = new CaptionSnapshotTracker();
  private readonly metadata = new YoutubeSourceMetadata();
  private trackKey: string | null = null;
  private lastKnownTrackKey: string | null = null;
  private nativeIdentity = "";
  private topic: string | undefined;
  private topicPromise: Promise<string> | undefined;
  private layoutKey = "";
  private missingCaptionAt: number | undefined;
  private lastVideoId: string | undefined;
  private pageKey: string;
  private activePlayer: HTMLElement | null = null;
  private activeCaptionKey: string | undefined;
  private sourceSequence = 0;
  private sourceRevision = 0;

  constructor(private readonly lifecycle: PageLifecycle, private readonly stream: CaptionStreamCoordinator,
    private readonly dependencies: CaptureDependencies) { this.pageKey = lifecycle.pageKey; }

  acceptNativeTrack(track: unknown, videoId: string | undefined): void { this.metadata.accept(track, videoId); }
  isCurrentSequence(sequence: number): boolean { return sequence === this.sourceSequence; }
  resetPage(): void {
    this.generationReset(); this.pageKey = this.lifecycle.pageKey; this.topic = undefined; this.topicPromise = undefined; this.trackKey = null;
    this.lastKnownTrackKey = null; this.nativeIdentity = ""; this.metadata.reset(); this.lastVideoId = undefined;
  }
  invalidate(): void { this.generationReset(); }
  dispose(): void { this.generationReset(); this.topicPromise = undefined; }
  private generationReset(): void {
    this.missingCaptionAt = undefined; this.layoutKey = ""; this.viewport.reset(); this.snapshots.reset();
    this.topicPromise = undefined;
    this.activeCaptionKey = undefined; this.activePlayer = null; this.sourceRevision++; this.sourceSequence++;
    this.stream.clear(this.sourceSequence);
  }
  readSource(): CaptionSource | undefined {
    const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
    const video = player?.querySelector<HTMLVideoElement>("video");
    if (!this.lifecycle.enabled || !player || !video || video.ended || this.lifecycle.stopped || document.hidden ||
        this.lifecycle.seeking || this.lifecycle.navigating || player.classList.contains("ad-showing")) return undefined;
    return readCaptionSource(this.viewport.read(player));
  }
  currentCaption(): string | undefined { return this.readSource()?.caption; }
  private liveCaption(videoId: string, revision: number, caption: string, identity: string, trackKey: string | null): string | undefined {
    if (this.lifecycle.videoId !== videoId || this.sourceRevision !== revision ||
        this.activeCaptionKey !== `${videoId}\u0000${revision}\u0000${caption}`) return undefined;
    const source = this.readSource();
    if (!source || source.caption !== caption) return undefined;
    const video = document.querySelector<HTMLVideoElement>(".html5-video-player video, #movie_player video");
    const time = Math.max(0, Math.floor((Number.isFinite(video?.currentTime) ? video!.currentTime : 0) * 1000));
    const matched = this.metadata.match(caption, time);
    if (trackKey && matched.trackKey && trackKey !== matched.trackKey) return undefined;
    const firstWord = caption.match(/\S+/u)?.[0] ?? caption;
    const metadata = matched.metadata(firstWord, 0);
    const liveIdentity = matched.trackKey && metadata.startMs !== null
      ? `${matched.trackKey}:${metadata.windowId}:${metadata.startMs}:${metadata.offsetMs}` : "";
    if (identity && liveIdentity && identity !== liveIdentity) return undefined;
    return caption;
  }

  private clearSource(): void {
    this.missingCaptionAt = undefined; this.viewport.reset(); this.snapshots.reset();
    this.topicPromise = undefined;
    this.sourceSequence++; this.stream.clear(this.sourceSequence);
  }
  private acceptResult(generation: number, sequence: number, videoId: string, revision: number,
    caption: string, identity: string, trackKey: string | null): boolean {
    return sequence === this.sourceSequence && revision === this.sourceRevision &&
      this.lifecycle.isCurrent(generation, videoId) &&
      this.liveCaption(videoId, revision, caption, identity, trackKey) !== undefined;
  }

  async capture(): Promise<void> {
    const player = document.querySelector<HTMLElement>(".html5-video-player, #movie_player");
    if (this.pageKey !== this.lifecycle.pageKey) this.resetPage();
    if (this.activePlayer !== player || (player !== null && !document.getElementById(OVERLAY_ID))) {
      this.activePlayer = player; this.activeCaptionKey = undefined; this.sourceRevision++; this.clearSource();
    }
    const video = player?.querySelector<HTMLVideoElement>("video");
    const videoId = this.lifecycle.videoId;
    const source = this.readSource();
    const caption = source?.caption;
    if (!video || !videoId || !caption) {
      const nativeContainer = player?.querySelector<HTMLElement>("#ytp-caption-window-container");
      const transientGap = caption === undefined && video && !video.ended && videoId && this.lifecycle.enabled &&
        !document.hidden && !this.lifecycle.seeking && !this.lifecycle.navigating && !this.lifecycle.stopped &&
        !player?.classList.contains("ad-showing") && nativeContainer?.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      if (transientGap) {
        // 哈希待决时尚无已提交快照；恢复同文也必须重新进入交付校验。
        if (!this.topic) this.activeCaptionKey = undefined;
        this.missingCaptionAt ??= this.dependencies.now(); this.dependencies.onSource(undefined);
      } else if (this.activeCaptionKey !== undefined) {
        this.activeCaptionKey = undefined; this.sourceRevision++; this.clearSource();
      }
      return;
    }
    const wasMissing = this.missingCaptionAt !== undefined;
    if (wasMissing && this.dependencies.now() - this.missingCaptionAt! > 650) {
      this.activeCaptionKey = undefined; this.sourceRevision++; this.clearSource();
    }
    this.missingCaptionAt = undefined;

    if (this.lastVideoId !== videoId) {
      this.lastVideoId = videoId;
      this.activeCaptionKey = undefined; this.sourceRevision++; this.clearSource();
    }
    const videoTimeMs = Math.max(0, Math.floor((Number.isFinite(video.currentTime) ? video.currentTime : 0) * 1000));
    const matched = this.metadata.match(caption, videoTimeMs);
    const firstWord = caption.match(/\S+/u)?.[0] ?? caption;
    const firstMetadata = matched.metadata(firstWord, 0);
    const nextIdentity = matched.trackKey && firstMetadata.startMs !== null
      ? `${matched.trackKey}:${firstMetadata.windowId}:${firstMetadata.startMs}:${firstMetadata.offsetMs}` : "";
    const textUnchanged = this.activeCaptionKey === `${videoId}\u0000${this.sourceRevision}\u0000${caption}`;
    if ((matched.trackKey !== null && this.lastKnownTrackKey !== null && matched.trackKey !== this.lastKnownTrackKey) ||
        (textUnchanged && this.nativeIdentity && nextIdentity && this.nativeIdentity !== nextIdentity)) {
      this.sourceRevision++; this.clearSource(); this.activeCaptionKey = undefined;
    }
    if (matched.trackKey !== null) this.lastKnownTrackKey = matched.trackKey;
    if (nextIdentity) this.nativeIdentity = nextIdentity;
    this.trackKey = matched.trackKey;
    const key = `${videoId}\u0000${this.sourceRevision}\u0000${caption}`;
    const nextLayoutKey = JSON.stringify(source.lineBreaks ?? []);
    if (key === this.activeCaptionKey) {
      if (nextLayoutKey !== this.layoutKey || wasMissing) { this.layoutKey = nextLayoutKey; this.dependencies.onSource(source); }
      return;
    }
    this.layoutKey = nextLayoutKey; this.activeCaptionKey = key;
    const observedAt = this.dependencies.now(); this.dependencies.onObserved();
    const ownSequence = ++this.sourceSequence;
    if (caption.length > MAX_CAPTION_LENGTH) {
      this.dependencies.onOversized(); this.activeCaptionKey = undefined; this.clearSource(); return;
    }
    const snapshot = this.snapshots.capture(source, matched.metadata);
    const expectedGeneration = this.lifecycle.generation;
    const expectedRevision = this.sourceRevision;
    if (!this.topic) {
      this.dependencies.onWaiting();
      const expectedVideo = videoId;
      const promise = this.topicPromise ??= this.dependencies.sha256(`youtube\u0000${videoId}`);
      let resolved: string;
      try { resolved = await promise; }
      catch {
        if (this.acceptResult(expectedGeneration, ownSequence, expectedVideo, expectedRevision, caption, nextIdentity, matched.trackKey)) {
          if (this.topicPromise === promise) this.topicPromise = undefined;
          this.activeCaptionKey = undefined;
        }
        return;
      }
      if (!this.acceptResult(expectedGeneration, ownSequence, expectedVideo, expectedRevision, caption, nextIdentity, matched.trackKey)) return;
      this.topic = resolved;
    }
    if (!this.acceptResult(expectedGeneration, ownSequence, videoId, expectedRevision, caption, nextIdentity, matched.trackKey)) return;
    const request: CaptionHintRequest = { captionTopicKey: this.topic, trackKey: this.trackKey,
      lastRequestedSnapshot: null, currentSnapshot: snapshot };
    this.dependencies.onAcquisition(this.dependencies.now() - observedAt);
    const event: CaptionEvent = { key, sequence: ownSequence, videoTimeMs, request };
    this.stream.submit(event);
  }
}
