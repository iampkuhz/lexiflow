package io.lexiflow.lexicon.application;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.domain.LexiconHintEligibility;
import io.lexiflow.lexicon.domain.LexiconPriority;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

class GlossPreparationTest {
  @Test
  void mergesOnlyIdenticalExpressionsAndIsIdempotent() {
    for (var gloss : List.of("平行四边形；[机] 平行四边形", "[数] 六边形; 六边形", "六边形；六边形")) {
      var prepared = GlossPreparation.normalize(gloss);
      assertFalse(prepared.contains("；"));
      assertFalse(prepared.contains("["));
      assertEquals(prepared, GlossPreparation.normalize(prepared));
    }
    for (var gloss :
        List.of(
            "银行；河岸", "六边形；六角形", "六边形；", ";六边形", "六边形；[数]", "六边形；[数] 六边形,六角形", "六边形；[<b>] 六边形")) {
      assertEquals(gloss, GlossPreparation.normalize(gloss));
    }
    assertEquals("可靠的", GlossPreparation.normalize("可靠的"));
  }

  @Test
  void importPreparationMarksBasicFormsAndLeavesWholePhrasesIndependent() {
    var basic = row("the", "那", List.of(), List.of());
    assertTrue(basic.basicVocabulary());
    assertFalse(basic.prewarmEligible());
    assertTrue(row("exist", "存在", List.of(), List.of("was")).basicVocabulary());
    assertFalse(row("on the contrary", "相反", List.of(), List.of()).basicVocabulary());
    var normalized = row("parallelogram", "平行四边形；[机] 平行四边形", List.of(), List.of());
    assertEquals("平行四边形", normalized.chineseGloss());
    var plan =
        LexiconImportPlan.prepare(List.of(basic, normalized), 1, "a".repeat(64), Instant.EPOCH);
    assertEquals(
        LexiconHintEligibility.BASIC_VOCABULARY, plan.getFirst().entry().hintEligibility());
    assertEquals(LexiconHintEligibility.CANDIDATE, plan.getLast().entry().hintEligibility());
    assertEquals("fixture#1", plan.getLast().entry().senses().getFirst().provenanceReference());
  }

  private static LexiconImportRow row(
      String lemma, String gloss, List<String> aliases, List<String> forms) {
    var ref = new SourceReference("fixture", "MIT", "fixture#1");
    return new LexiconImportRow(
        lemma,
        gloss,
        "definition",
        aliases,
        forms,
        LexiconPriority.unranked(),
        ref,
        ref,
        List.of(),
        true);
  }
}
