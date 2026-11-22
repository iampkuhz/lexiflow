package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 合成输入；预期取自独立术语事实，不是实际观看样本。 */
class ContextPhrasePolicyTest {
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  @Test
  void zeroPriorityCompletePhraseWinsItsRankedComponentButDoesNotReplaceOtherSenses() {
    for (var pair :
        List.of(
            List.of("neural tissue", "神经组织"),
            List.of("fatty tissue", "脂肪组织"),
            List.of("net force", "合力"),
            List.of("nerve cell", "神经细胞"),
            List.of("spinal cord", "脊髓"))) {
      String phrase = pair.getFirst();
      String word =
          switch (phrase) {
            case "net force" -> "net";
            case "nerve cell" -> "cell";
            case "spinal cord" -> "cord";
            default -> "tissue";
          };
      var candidates =
          List.of(
              candidate(1, word, word, "旧首义", 910, false),
              candidate(2, phrase, phrase, pair.getLast(), 0, true));
      assertEquals(List.of(pair.getLast()), glosses(phrase, candidates));
      assertEquals(List.of("旧首义"), glosses(word, candidates));
    }
    var tissue = candidate(1, "tissue", "tissue", "薄的织物", 910, false);
    var reviewed = candidate(2, "neural tissue", "neural tissue", "神经组织", 0, true);
    assertEquals(List.of("薄的织物"), glosses("paper tissue", List.of(tissue, reviewed)));
    assertEquals(List.of("薄的织物"), glosses("woven tissue", List.of(tissue, reviewed)));
    assertEquals(List.of("薄的织物"), glosses("unreviewed tissue", List.of(tissue, reviewed)));
  }

  @Test
  void onlyExplicitInflectionsMatchAndRepeatedOccurrencesStayDistinct() {
    var plural = candidate(2, "neural tissues", "neural tissue", "神经组织", 0, true);
    assertEquals(
        List.of("神经组织", "神经组织"), glosses("neural tissues and neural tissues", List.of(plural)));
    assertEquals(List.of(), glosses("neural tissue", List.of(plural)));
    assertEquals(List.of(), glosses("neural tissuesx", List.of(plural)));
    assertEquals(List.of(), glosses("neural, tissues", List.of(plural)));
  }

  @Test
  void blockedAmbiguousAndMissingPhrasesDoNotSuppressUncoveredWordRisk() {
    var word = candidate(1, "tissue", "tissue", "薄的织物", 910, false);
    var phrase = candidate(2, "neural tissue", "neural tissue", "神经组织", 0, true);
    var block =
        new LexiconHintCandidate(
            3,
            null,
            1,
            "neural tissue",
            LexiconHintAction.BLOCK,
            null,
            0);
    assertEquals(List.of("薄的织物"), glosses("neural tissue", List.of(word)));
    assertEquals(List.of("薄的织物"), glosses("neural tissue", List.of(word, block)));
    assertEquals(List.of("薄的织物"), glosses("neural tissue", List.of(word, phrase, block)));
    var unsafe = candidate(4, "neural tissue", "neural tissue", "<script>", 0, true);
    assertEquals(List.of("薄的织物"), glosses("neural tissue", List.of(word, unsafe)));
  }

  @Test
  void crossingAndPhraseConflictsKeepPriorityAndRejectedPhraseLeavesWordAvailable() {
    // 前一短语与完整 neural tissue 交叉但不含 tissue；它胜出后 tissue 仍须可见。
    var candidates =
        List.of(
            candidate(1, "neural tissue", "neural tissue", "神经组织", 0, true),
            candidate(2, "sample neural", "sample neural", "交叉短语", 10, true),
            candidate(3, "tissue", "tissue", "薄的织物", 910, false));
    assertEquals(List.of("交叉短语", "薄的织物"), glosses("sample neural tissue", candidates));
    var reversed = new ArrayList<>(candidates);
    Collections.reverse(reversed);
    assertEquals(
        glosses("sample neural tissue", candidates), glosses("sample neural tissue", reversed));
    var larger = candidate(4, "sample neural tissue", "sample neural tissue", "大短语", 20, true);
    assertEquals(
        List.of("大短语"), glosses("sample neural tissue", List.of(candidates.getFirst(), larger)));
  }

  private List<String> glosses(String text, List<LexiconHintCandidate> candidates) {
    return policy.evaluateSelection(text, 0, text.length(), 0, candidates).hints().stream()
        .map(h -> h.chineseGloss())
        .toList();
  }

  private static LexiconHintCandidate candidate(
      long id, String form, String lemma, String gloss, int priority, boolean phrase) {
    return new LexiconHintCandidate(
        id,
        new UUID(0, id),
        1,
        form,
        LexiconHintAction.HINT,
        gloss,
        priority);
  }
}
