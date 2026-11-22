package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.List;
import org.junit.jupiter.api.Test;

/** 验证已冻结的阻断决定不能被观看阶段重新解释为提示。 */
class PreparedHintEligibilityTest {
  @Test
  void doesNotDisplayBlockedVocabulary() {
    var policy = new DeterministicHintPolicy();
    var caption = "hexagon";
    var blocked =
        new LexiconHintCandidate(1L, null, 1, caption, LexiconHintAction.BLOCK, null, 0, null);
    assertTrue(
        policy
            .evaluateSelection(caption, 0, caption.length(), 0, List.of(blocked))
            .hints()
            .isEmpty());
  }
}
