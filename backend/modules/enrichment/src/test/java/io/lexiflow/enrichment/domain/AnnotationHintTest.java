package io.lexiflow.enrichment.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class AnnotationHintTest {
  private static final String ENTRY_ID = "00112233-4455-6677-8899-aabbccddeeff";
  private static final String SENSE_ID = "ffeeddcc-bbaa-9988-7766-554433221100";

  @Test
  void retainsCanonicalEntryAndSenseIds() {
    var hint = new AnnotationHint(0, 8, ENTRY_ID, SENSE_ID, 1, "可靠的");

    assertEquals(ENTRY_ID, hint.lexiconEntryId());
    assertEquals(SENSE_ID, hint.senseId());
  }

  @Test
  void rejectsMissingAndNonCanonicalSourceIdentities() {
    for (var invalid : new String[] {"", " ", "not-a-uuid", ENTRY_ID.toUpperCase()}) {
      assertThrows(
          IllegalArgumentException.class,
          () -> new AnnotationHint(0, 8, invalid, SENSE_ID, 1, "可靠的"));
      assertThrows(
          IllegalArgumentException.class,
          () -> new AnnotationHint(0, 8, ENTRY_ID, invalid, 1, "可靠的"));
    }
  }
}
