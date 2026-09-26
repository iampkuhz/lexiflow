package io.lexiflow.lexicon.application.importing.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.util.List;
import org.junit.jupiter.api.Test;

/** 覆盖导入时冻结提示或阻断的独立规则。 */
class HintPreparationTest {
  @Test
  void blocksBasicLowInformationAndAmbiguousGlossButNotRankedWord() {
    assertEquals("basic_vocabulary", HintPreparation.exclusionReason(row("the", "这个")));
    for (var phrase : List.of("the first", "not in", "on yesterday", "to be")) {
      assertEquals("low_information_phrase", HintPreparation.exclusionReason(row(phrase, "错误短释")));
    }
    assertEquals(
        "unsafe_or_ambiguous_gloss",
        HintPreparation.exclusionReason(row("sustainability", "持续性；可持续性")));
    assertNull(HintPreparation.exclusionReason(row("literally", "按字面意思")));
  }

  private static LexiconImportRow row(String lemma, String gloss) {
    var source = new SourceReference("fixture", "MIT", "row-" + lemma);
    return new LexiconImportRow(
        lemma,
        gloss,
        "",
        List.of(),
        List.of(),
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true);
  }
}
