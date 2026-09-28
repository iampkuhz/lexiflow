package io.lexiflow.lexicon.application.importing.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.model.ImportClassification;
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
        "unsafe_default_candidate",
        HintPreparation.exclusionReason(row("sustainability", "持续性；可持续性")));
    assertNull(HintPreparation.exclusionReason(row("literally", "按字面意思")));
  }

  @Test
  void blocksEveryOxfordWordRegardlessOfRankButKeepsCompletePhrasesIndependent() {
    for (Long rank : new Long[] {null, 2370L, 10000L}) {
      var word = oxfordRow("false", "错误的", rank);
      assertTrue(word.basicVocabulary());
      assertFalse(word.prewarmEligible());
      assertEquals("basic_vocabulary", HintPreparation.exclusionReason(word));
    }
    var phrase = oxfordRow("false alarm", "误报", null);
    assertFalse(phrase.basicVocabulary());
    assertNull(HintPreparation.exclusionReason(phrase));
    assertFalse(row("specialist", "专家").basicVocabulary());
    assertNull(HintPreparation.exclusionReason(row("specialist", "专家")));
  }

  @Test
  void keepsFrequencyEvidenceAndPrewarmSeparateFromHintEligibility() {
    var unknown =
        new LexiconImportRow(
            "obscure",
            "罕见词",
            "",
            List.of(),
            List.of(),
            LexiconPriority.fromRankedEvidence(4.2, 1, false),
            new SourceReference("ecdict-stardict", "fixture", "row-obscure"),
            new SourceReference("ecdict-stardict", "fixture", "row-obscure"),
            List.of(),
            true,
            false,
            "fixture",
            "罕见词",
            null,
            null,
            List.of(),
            false,
            false,
            false);
    var unknownPrepared = HintPreparation.prepare(unknown);
    assertEquals(
        ImportClassification.FrequencyEvidence.UNKNOWN,
        unknownPrepared.classification().frequencyEvidence());
    assertTrue(unknownPrepared.classification().hintEligible());
    assertTrue(unknownPrepared.classification().prewarmEligible());

    var known =
        new LexiconImportRow(
            "false alarm",
            "误报",
            "",
            List.of(),
            List.of(),
            new LexiconPriority(4.2, 1, 900),
            new SourceReference("fixture", "MIT", "phrase"),
            new SourceReference("fixture", "MIT", "phrase"),
            List.of(),
            true,
            false,
            "fixture",
            "误报",
            null,
            null,
            List.of(),
            false,
            false,
            false);
    var knownPrepared = HintPreparation.prepare(known);
    assertEquals(
        ImportClassification.FrequencyEvidence.KNOWN,
        knownPrepared.classification().frequencyEvidence());
    assertTrue(knownPrepared.classification().hintEligible());
    assertFalse(knownPrepared.classification().basicWord());
  }

  private static LexiconImportRow oxfordRow(String lemma, String gloss, Long rank) {
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
        true,
        false,
        "oxford-all-words-fixture",
        gloss,
        rank,
        null,
        List.of(),
        true,
        false,
        false);
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
