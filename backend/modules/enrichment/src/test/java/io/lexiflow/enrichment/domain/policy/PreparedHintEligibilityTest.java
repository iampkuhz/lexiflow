package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.HintState;
import io.lexiflow.lexicon.domain.model.LexiconEntry;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintEligibility;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import io.lexiflow.lexicon.domain.model.LexiconProvenance;
import io.lexiflow.lexicon.domain.model.LexiconSense;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class PreparedHintEligibilityTest {
  @Test
  void doesNotDisplayUnprocessedOrBasicVocabularyEntries() {
    var policy = new DeterministicHintPolicy();
    var caption = "hexagon";
    var context =
        new CaptionContext(UUID.randomUUID(), 1, "a".repeat(64), caption, 0, caption.length());

    for (var eligibility :
        List.of(LexiconHintEligibility.UNPROCESSED, LexiconHintEligibility.BASIC_VOCABULARY)) {
      var entry = entry(eligibility);
      assertEquals(HintState.NO_PENDING, policy.evaluate(context, List.of(entry)).state());
    }
  }

  private static LexiconEntry entry(LexiconHintEligibility eligibility) {
    return new LexiconEntry(
        UUID.randomUUID(),
        1,
        "en",
        LexiconEntryKind.WORD,
        "hexagon",
        List.of(new LexiconSense(UUID.randomUUID(), "六边形", "", "fixture")),
        List.of(),
        List.of(),
        new LexiconProvenance("fixture", "MIT", "a".repeat(64), Instant.EPOCH),
        LexiconPriority.unranked(),
        eligibility);
  }
}
