package io.lexiflow.enrichment.domain.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ContentRevisionTest {
  private static final UUID CONTENT_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");

  @Test
  void derivesStableSegmentIdentityAndBindsCaptionContext() {
    var segment = segment(1, 0, 1000, " Reliable   caption ");
    var revision =
        new ContentRevision(CONTENT_ID, 1, "youtube", digest("source"), 1, List.of(segment));

    var context = CaptionContext.from(revision, segment, 0, 8);

    assertEquals("Reliable caption", context.caption());
    assertEquals(segment.segmentId(), context.segmentId());
  }

  @Test
  void rejectsARevisionWhoseSegmentIdentityDoesNotBindItsInput() {
    var segment = new CaptionSegment(digest("wrong"), "en", 0, 0, 1000, "caption");

    assertThrows(
        IllegalArgumentException.class,
        () -> new ContentRevision(CONTENT_ID, 1, "youtube", digest("source"), 1, List.of(segment)));
  }

  private static CaptionSegment segment(
      long sequence, long startMillis, long endMillis, String text) {
    var normalized = CaptionSegment.normalizeText(text);
    return new CaptionSegment(
        ContentRevision.segmentIdFor(
            CONTENT_ID, 1, 1, "en", sequence, startMillis, endMillis, normalized),
        "en",
        sequence,
        startMillis,
        endMillis,
        normalized);
  }

  private static String digest(String value) {
    return ContentRevision.segmentIdFor(CONTENT_ID, 1, 1, "digest", 0, 0, 1, value);
  }
}
