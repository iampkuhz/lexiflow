package io.lexiflow.enrichment.domain.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class AnnotationHintTest {
  private static final String ENTRY_ID = "9007199254740993";
  private static final String SENSE_ID = "ffeeddcc-bbaa-9988-7766-554433221100";

  @Test
  void retainsCanonicalEntryAndSenseIds() {
    var hint = new AnnotationHint(0, 8, ENTRY_ID, SENSE_ID, 1, "可靠的");

    assertEquals(ENTRY_ID, hint.lexiconEntryId());
    assertEquals(SENSE_ID, hint.senseId());
  }

  @Test
  void acceptsLargestPositiveLongWithoutNumberPrecisionLoss() {
    var hint = new AnnotationHint(0, 8, Long.toString(Long.MAX_VALUE), SENSE_ID, 1, "可靠的");
    assertEquals("9223372036854775807", hint.lexiconEntryId());
  }

  @Test
  void rejectsMissingAndNonCanonicalSourceIdentities() {
    for (var invalid :
        new String[] {"", " ", "not-a-uuid", "0", "-1", "01", "00", "9223372036854775808"}) {
      assertThrows(
          IllegalArgumentException.class,
          () -> new AnnotationHint(0, 8, invalid, SENSE_ID, 1, "可靠的"));
      assertThrows(
          IllegalArgumentException.class,
          () -> new AnnotationHint(0, 8, ENTRY_ID, invalid, 1, "可靠的"));
    }
    assertThrows(
        IllegalArgumentException.class,
        () -> new AnnotationHint(0, 8, ENTRY_ID, SENSE_ID.toUpperCase(), 1, "可靠的"));
    assertThrows(
        NullPointerException.class, () -> new AnnotationHint(0, 8, null, SENSE_ID, 1, "可靠的"));
    assertThrows(
        NullPointerException.class, () -> new AnnotationHint(0, 8, ENTRY_ID, null, 1, "可靠的"));
  }
}
