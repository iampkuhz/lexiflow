import type { SourceMetadata } from "./caption-snapshot";
export type NativeFragment = { text: string; startMs: number; endMs: number; offsetMs: number | null; windowId: string | null; append: boolean };
export type NativeTrack = { videoId: string; trackKey: string; fragments: NativeFragment[] };
const norm = (value: string): string => value.normalize("NFC").replace(/\s+/g, " ").trim();
/** 严格接收不可信页面桥消息；仅作为 DOM 已见文字的元信息，不产生英文。 */
export class YoutubeSourceMetadata {
  private tracks: NativeTrack[] = [];
  accept(value: unknown, videoId: string | undefined): void {
    if (!value || typeof value !== "object") return;
    const track = value as NativeTrack;
    if (!videoId || track.videoId !== videoId || typeof track.trackKey !== "string" || track.trackKey.length > 128 ||
        !track.trackKey.length || !Array.isArray(track.fragments) || track.fragments.length > 2000) return;
    let length = 0;
    for (const fragment of track.fragments) {
      if (!fragment || typeof fragment.text !== "string" || (length += fragment.text.length) > 200000 ||
          ![fragment.startMs, fragment.endMs].every(value => Number.isSafeInteger(value) && value >= 0) ||
          (fragment.offsetMs !== null && (!Number.isSafeInteger(fragment.offsetMs) || fragment.offsetMs < 0)) ||
          fragment.endMs <= fragment.startMs || fragment.endMs < fragment.startMs + (fragment.offsetMs ?? 0) || typeof fragment.append !== "boolean" ||
          (fragment.windowId !== null && (typeof fragment.windowId !== "string" || fragment.windowId.length > 128))) return;
    }
    this.tracks = [...this.tracks.filter(old => old.videoId === videoId && old.trackKey !== track.trackKey), structuredClone(track)].slice(-4);
  }
  match(caption: string, mediaTimeMs: number): { trackKey: string | null; metadata: (text: string, offset: number) => SourceMetadata } {
    const candidates = this.tracks.map(track => ({ track, active: track.fragments.filter(fragment =>
      fragment.startMs + (fragment.offsetMs ?? 0) <= mediaTimeMs && fragment.endMs > mediaTimeMs) }));
    const matched = candidates.filter(candidate => {
      const activeText = norm(candidate.active.map(fragment => fragment.text).join(""));
      return activeText.length > 0 && activeText.includes(norm(caption));
    });
    if (matched.length !== 1) return { trackKey: null, metadata: () => ({ windowId: null, startMs: null, offsetMs: null }) };
    const candidate = matched[0];
    return { trackKey: candidate.track.trackKey, metadata: text => {
      const matches = candidate.active.filter(fragment => norm(fragment.text).includes(norm(text)) && norm(text).length > 0);
      if (matches.length !== 1) return { windowId: null, startMs: null, offsetMs: null };
      const fragment = matches[0];
      return { windowId: fragment.windowId, startMs: fragment.startMs, offsetMs: fragment.offsetMs };
    } };
  }
  reset(): void { this.tracks = []; }
}
