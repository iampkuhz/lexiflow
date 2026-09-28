package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class CandidateFormsTest {
  @Test
  void usesSharedTokenRulesAndContiguousWindowWithoutChangingUnicodeBoundaries() {
    assertEquals(List.of("don't", "don't stop", "stop"), CandidateForms.fromCaption("Don't STOP"));
    assertEquals(List.of("co", "co operate", "operate"), CandidateForms.fromCaption("co-operate"));
    assertEquals(
        List.of("𐐨abc", "𐐨abc world", "world"), CandidateForms.fromCaption("𐐀abc world"));
  }
}
