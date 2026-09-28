package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class PublishedCandidateEligibilityTest {
  @Test
  void acceptsPublishedSafeHintVerbatimIncludingLicensedShortPhrases() {
    var hint = candidate("the first", LexiconHintAction.HINT, "第一");
    assertTrue(PublishedCandidateEligibility.isDisplayable(hint));
    assertTrue(
        PublishedCandidateEligibility.isDisplayable(
            candidate("bank", LexiconHintAction.HINT, "可靠的")));
  }

  @Test
  void rejectsBlockAndUnsafeGlossWithoutRewritingIt() {
    assertFalse(
        PublishedCandidateEligibility.isDisplayable(
            candidate("bank", LexiconHintAction.BLOCK, null)));
    for (var gloss : new String[] {"bank", "一".repeat(25), "可靠\u0000", "可靠（结果）", "<b>可靠</b>"}) {
      assertFalse(
          PublishedCandidateEligibility.isDisplayable(
              candidate("bank", LexiconHintAction.HINT, gloss)));
    }
  }

  @Test
  void countsCodePointsRatherThanUtf16UnitsAndRejectsUnsafeUnicodeWithoutCleaning() {
    assertTrue(
        PublishedCandidateEligibility.isDisplayable(
            candidate("bank", LexiconHintAction.HINT, "\uD840\uDC00".repeat(24))));
    for (var gloss :
        new String[] {
          "\uD840\uDC00".repeat(25),
          "银 行",
          "银行\u00a0",
          "银行\u200b",
          "银行\uD800",
          "银行\uE000",
          "银行_",
          "银行-",
          "银行&",
          "银行🤖"
        }) {
      assertFalse(
          PublishedCandidateEligibility.isDisplayable(
              candidate("bank", LexiconHintAction.HINT, gloss)),
          gloss);
    }
  }

  private static LexiconHintCandidate candidate(
      String form, LexiconHintAction action, String gloss) {
    return new LexiconHintCandidate(
        UUID.randomUUID(),
        action == LexiconHintAction.HINT ? UUID.randomUUID() : null,
        1,
        "en",
        form,
        form,
        LexiconEntryKind.PHRASE,
        action,
        gloss,
        1,
        0,
        0);
  }
}
