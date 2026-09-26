package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.HintState;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 验证已冻结的阻断决定不能被观看阶段重新解释为提示。 */
class PreparedHintEligibilityTest {
  @Test
  void doesNotDisplayBlockedVocabulary() {
    var policy = new DeterministicHintPolicy();
    var caption = "hexagon";
    var context =
        new CaptionContext(UUID.randomUUID(), 1, "a".repeat(64), caption, 0, caption.length());
    var blocked =
        new LexiconHintCandidate(
            UUID.randomUUID(),
            null,
            1,
            "en",
            caption,
            caption,
            LexiconEntryKind.WORD,
            LexiconHintAction.BLOCK,
            null,
            0,
            0,
            0);
    assertEquals(HintState.NO_PENDING, policy.evaluate(context, List.of(blocked)).state());
  }
}
