package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class AnnotationHintResponseTest {
  private static final String ENTRY_ID = "00112233-4455-6677-8899-aabbccddeeff";
  private static final String SENSE_ID = "ffeeddcc-bbaa-9988-7766-554433221100";

  @Test
  void rejectsNonCanonicalSourceIds() {
    assertThrows(
        IllegalArgumentException.class,
        () -> new AnnotationHintResponse(0, 8, ENTRY_ID.toUpperCase(), SENSE_ID, 1, "可靠的"));
    assertThrows(
        IllegalArgumentException.class,
        () -> new AnnotationHintResponse(0, 8, ENTRY_ID, "not-a-uuid", 1, "可靠的"));
  }
}
