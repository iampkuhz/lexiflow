package io.lexiflow.enrichment.application.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class LocalPreferencesTest {
  @Test
  void restrictsCutoffsAndCanonicalIdsAndNormalizesLemma() {
    var store = new RecordingStore();
    var useCase = new LocalPreferences(store);
    assertEquals(1500, useCase.read().commonWordCutoff());
    assertEquals(2000, useCase.setCutoff(2000).commonWordCutoff());
    assertThrows(IllegalArgumentException.class, () -> useCase.setCutoff(1001));
    assertThrows(IllegalArgumentException.class, () -> useCase.hide("01", "word", "gloss"));
    var id = Long.toString(io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId("Cats"));
    useCase.hide(id, "Cats", "cats");
    assertEquals("cats", store.lastLemma);
  }

  private static final class RecordingStore implements LocalPreferences.Store {
    String lastLemma;
    int cutoff = 1500;

    public LocalPreferences.State read() {
      return new LocalPreferences.State(cutoff, List.of());
    }

    public LocalPreferences.State setCutoff(int c) {
      cutoff = c;
      return read();
    }

    public LocalPreferences.State hide(String id, String lemma, String gloss) {
      lastLemma = lemma;
      return read();
    }

    public LocalPreferences.State restore(String id) {
      return read();
    }
  }
}
