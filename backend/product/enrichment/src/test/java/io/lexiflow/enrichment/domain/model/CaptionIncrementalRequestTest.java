package io.lexiflow.enrichment.domain.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class CaptionIncrementalRequestTest {
  @Test
  void validatesRetainedKeyUsingOnlyThePreviousText() {
    var previous = List.of(new CaptionIncrementalRequest.PreviousSegment("a", "old word"));
    var current = snapshot(segment("a", "word"), segment("b", " new"));
    assertEquals(current, new CaptionIncrementalRequest(previous, current).current());
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest(previous, snapshot(segment("a", "word mutated"))));
  }

  @Test
  void rejectsDuplicatesAndExcessiveTextInBothLists() {
    assertThrows(
        IllegalArgumentException.class, () -> new CaptionIncrementalRequest.Group(List.of()));
    assertThrows(
        IllegalArgumentException.class, () -> snapshot(segment("a", "one"), segment("a", "two")));
    assertThrows(IllegalArgumentException.class, () -> snapshot(segment("a", "x".repeat(501))));
    var previous = new CaptionIncrementalRequest.PreviousSegment("a", "word");
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest(List.of(previous, previous), snapshot()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new CaptionIncrementalRequest(List.of(previous), snapshot(segment("b", "word")), 1000));
    var longPrevious = new CaptionIncrementalRequest.PreviousSegment("b", "x".repeat(497));
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest(List.of(previous, longPrevious), snapshot()));
  }

  @Test
  void validatesCurrentLineAndUnicodeInBothLists() {
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest.Segment("a", "x", true, -1));
    assertThrows(IllegalArgumentException.class, () -> segment("a", "\uD800"));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new CaptionIncrementalRequest(
                List.of(new CaptionIncrementalRequest.PreviousSegment("a", "\uDC00")),
                snapshot(segment("a", "word")),
                1500));
    assertEquals(2, segment("a", "😀").text().length());
  }

  private static CaptionIncrementalRequest.Snapshot snapshot(
      CaptionIncrementalRequest.Segment... segments) {
    return new CaptionIncrementalRequest.Snapshot(
        segments.length == 0
            ? List.of()
            : List.of(new CaptionIncrementalRequest.Group(List.of(segments))));
  }

  private static CaptionIncrementalRequest.Segment segment(String key, String text) {
    return new CaptionIncrementalRequest.Segment(key, text, true, 0);
  }
}
