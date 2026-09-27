package io.lexiflow.enrichment.domain.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class CaptionIncrementalRequestTest {
  @Test
  void acceptsPrefixTrimAndRejectsInPlaceGrowth() {
    var previous = snapshot(segment("a", "reliable", true));
    var current = snapshot(segment("a", "liable", true));
    var request = new CaptionIncrementalRequest("topic", null, previous, current);
    assertEquals("liable", request.current().captions().getFirst().segments().getFirst().text());
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new CaptionIncrementalRequest(
                "topic", null, previous, snapshot(segment("a", "reliable!", true))));
  }

  @Test
  void rejectsDuplicateKeysAndTotalUtf16Limit() {
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest.Group(null, null, List.of()));
    assertThrows(
        IllegalArgumentException.class,
        () -> snapshot(segment("a", "one", true), segment("a", "two", true)));
    assertThrows(
        IllegalArgumentException.class,
        () -> snapshot(segment("a", "x".repeat(300), true), segment("b", "y".repeat(201), true)));
    assertEquals(
        500,
        snapshot(segment("a", "x".repeat(500), true))
            .captions()
            .getFirst()
            .segments()
            .getFirst()
            .text()
            .length());
  }

  @Test
  void rejectsUnsafeNumbersAndSplitSurrogates() {
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest.Segment("a", "x", 9_007_199_254_740_992L, true, 0));
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionIncrementalRequest.Segment("a", "x", null, true, -1));
    assertThrows(IllegalArgumentException.class, () -> segment("a", "\uD83E", true));
  }

  private static CaptionIncrementalRequest.Snapshot snapshot(
      CaptionIncrementalRequest.Segment... segments) {
    return new CaptionIncrementalRequest.Snapshot(
        List.of(new CaptionIncrementalRequest.Group(null, null, List.of(segments))));
  }

  private static CaptionIncrementalRequest.Segment segment(
      String key, String text, boolean append) {
    return new CaptionIncrementalRequest.Segment(key, text, null, append, 0);
  }
}
