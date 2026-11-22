package io.lexiflow.api.hints.model;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import java.util.*;

/** v3 请求只携带仍可见的旧片段与当前可见快照。 */
public record CaptionHintRequest(List<PreviousSegment> previousSegments, Snapshot currentSnapshot) {
  public CaptionHintRequest {
    previousSegments = List.copyOf(Objects.requireNonNull(previousSegments, "previousSegments"));
    Objects.requireNonNull(currentSnapshot, "currentSnapshot");
  }

  /** 映射到领域双快照合同；领域校验完整旧片段集合与可见快照。 */
  public CaptionIncrementalRequest toDomain() {
    return toDomain(1500);
  }

  public CaptionIncrementalRequest toDomain(int commonWordCutoff) {
    var currentGroups = currentSnapshot.captions().stream().map(CaptionGroup::toCurrent).toList();
    var old = previousSegments.stream()
        .map(s -> new CaptionIncrementalRequest.PreviousSegment(s.key(), s.text())).toList();
    return new CaptionIncrementalRequest(old, new CaptionIncrementalRequest.Snapshot(currentGroups), commonWordCutoff);
  }

  public record PreviousSegment(String key, String text) {}
  public record Snapshot(List<CaptionGroup> captions) {
    public Snapshot { captions = List.copyOf(Objects.requireNonNull(captions, "captions")); }
  }
  public record CaptionGroup(List<Segment> segments) {
    public CaptionGroup { segments = List.copyOf(Objects.requireNonNull(segments, "segments")); }
    CaptionIncrementalRequest.Group toCurrent() {
      return new CaptionIncrementalRequest.Group(null, null, segments.stream().map(Segment::toDomain).toList());
    }
  }
  public record Segment(String key, String text, Boolean append, Long line) {
    CaptionIncrementalRequest.Segment toDomain() {
      return new CaptionIncrementalRequest.Segment(key, text, null, Objects.requireNonNull(append), Objects.requireNonNull(line));
    }
  }
}
