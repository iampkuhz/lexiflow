package io.lexiflow.lexicon.domain.port;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class LexiconIdentityTest {
  @Test
  void entryIdentityIsStablePositiveAndUsesNormalizedLemma() {
    var first = LexiconIdentity.entryId("  Café   au lait ");
    assertEquals(first, LexiconIdentity.entryId("café au lait"));
    assertTrue(first > 0);
    assertTrue(Long.toString(first).length() <= 19);
    assertNotEquals(first, LexiconIdentity.entryId("coffee au lait"));
    assertEquals(4106666595944390966L, LexiconIdentity.entryId("reliable"));
    assertThrows(NullPointerException.class, () -> LexiconIdentity.entryId(null));
    assertThrows(IllegalArgumentException.class, () -> LexiconIdentity.entryId(" \t "));
  }

  @Test
  void senseIdentityDependsOnVersionAndNormalizedLemma() {
    assertEquals(LexiconIdentity.senseId(2, "  Reliable "), LexiconIdentity.senseId(2, "reliable"));
    assertNotEquals(LexiconIdentity.senseId(2, "reliable"), LexiconIdentity.senseId(3, "reliable"));
    assertThrows(NullPointerException.class, () -> LexiconIdentity.senseId(1, null));
    assertThrows(IllegalArgumentException.class, () -> LexiconIdentity.senseId(1, " "));
  }
}
