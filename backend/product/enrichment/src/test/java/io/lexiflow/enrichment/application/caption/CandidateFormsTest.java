package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class CandidateFormsTest {
  @Test
  void usesSharedTokenRulesAndContiguousWindowWithoutChangingUnicodeBoundaries() {
    assertEquals(List.of("don't", "don't stop", "stop"), CandidateForms.fromCaption("Don't STOP"));
    assertEquals(List.of("co-operate"), CandidateForms.fromCaption("co-operate"));
    assertEquals(
        List.of("𐐨abc", "𐐨abc world", "world"), CandidateForms.fromCaption("𐐀abc world"));
    assertEquals(
        List.of("non-stick", "non-stick pan", "pan"),
        CandidateForms.fromCaption("‘Non‑stick’ pan"));
    assertEquals(
        List.of("that's", "that's fine", "fine"), CandidateForms.fromCaption("That’s fine!"));
  }

  @Test
  void wholeWordQueryDoesNotExpandOrDeleteExistingOfflineEligibility() {
    assertEquals(List.of("w-l"), CandidateForms.fromCaption("w-l"));
    assertTrue(io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy.withinQueryWindow("w-l"));
    assertTrue(io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy.withinQueryWindow("non-stick"));
    assertFalse(
        io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy.withinQueryWindow("state-of-the-art"));
    assertFalse(io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy.withinQueryWindow("ab"));
  }
}
