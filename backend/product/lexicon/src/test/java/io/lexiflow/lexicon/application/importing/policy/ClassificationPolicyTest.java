package io.lexiflow.lexicon.application.importing.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.model.ImportClassification;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.PreparedHint;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.util.List;
import org.junit.jupiter.api.Test;

/** 覆盖来源证据与词形级预热，不从未知排名或预热推断提示资格。 */
class ClassificationPolicyTest {
  @Test
  void distinguishesActualStardictRankSourceFromExplicitCsvZipfIncludingZero() {
    var unknown =
        row("obscure", "罕见词", "ecdict-stardict", "ecdict-bnc-frq", null, null, false, 0, 0);
    var rankKnown =
        row("recondite", "深奥的", "ecdict-stardict", "ecdict-bnc-frq", null, 45000L, false, 1, 23);
    var csvZero =
        row("quasar", "类星体", "fixture-dictionary", "fixture-frequency", null, null, true, 0, 0);
    assertEquals(
        ImportClassification.FrequencyEvidence.UNKNOWN,
        HintPreparation.prepare(unknown).classification().frequencyEvidence());
    assertEquals(
        ImportClassification.FrequencyEvidence.KNOWN,
        HintPreparation.prepare(rankKnown).classification().frequencyEvidence());
    assertEquals(
        ImportClassification.FrequencyEvidence.KNOWN,
        HintPreparation.prepare(csvZero).classification().frequencyEvidence());
    assertTrue(HintPreparation.prepare(unknown).classification().hintEligible());
    assertTrue(HintPreparation.prepare(csvZero).classification().hintEligible());
    assertTrue(HintPreparation.prepare(rankKnown).classification().complexListed());
    assertEquals(
        0,
        ClassificationPolicy.cachePriority(
            csvZero, HintPreparation.prepare(csvZero).classification(), LexiconHintAction.HINT));
  }

  @Test
  void preservesBasicWordAndPhraseAliasBoundary() {
    var word =
        new LexiconImportRow(
            "orbit",
            "轨道",
            "",
            List.of("the"),
            List.of(),
            new LexiconPriority(4.2, 0, 900),
            source("fixture"),
            source("fixture"),
            List.of(),
            true);
    assertTrue(word.basicVocabulary());
    assertTrue(HintPreparation.prepare(word).classification().basicWord());
    assertFalse(HintPreparation.prepare(word).classification().hintEligible());
    var phrase =
        new LexiconImportRow(
            "stellar system",
            "恒星系",
            "",
            List.of("the"),
            List.of(),
            new LexiconPriority(4.2, 0, 900),
            source("fixture"),
            source("fixture"),
            List.of(),
            true,
            false,
            "fixture");
    assertFalse(phrase.basicVocabulary());
    assertFalse(HintPreparation.prepare(phrase).classification().basicWord());
    assertTrue(HintPreparation.prepare(phrase).classification().hintEligible());
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new LexiconImportRow(
                "stellar system",
                "恒星系",
                "",
                List.of(),
                List.of(),
                new LexiconPriority(4.2, 0, 900),
                source("fixture"),
                source("fixture"),
                List.of(),
                true,
                true,
                "fixture"));
  }

  @Test
  void derivesPositiveOrNegativeFromFinalActionAndOriginalScore() {
    var basic = row("the", "这个", "fixture", "fixture", null, null, true, 0, 888);
    var basicClassification = HintPreparation.prepare(basic).classification();
    assertEquals(
        1000,
        ClassificationPolicy.cachePriority(basic, basicClassification, LexiconHintAction.BLOCK));
    var blocked = row("opaque", "错误；多义", "fixture", "fixture", null, null, true, 0, 765);
    var blockedClassification = HintPreparation.prepare(blocked).classification();
    assertFalse(blockedClassification.hintEligible());
    assertTrue(blockedClassification.prewarmEligible());
    assertEquals(
        765,
        ClassificationPolicy.cachePriority(
            blocked, blockedClassification, LexiconHintAction.BLOCK));
    var hinted = row("luminous", "发光的", "fixture", "fixture", null, null, true, 0, 765);
    var hintedClassification = HintPreparation.prepare(hinted).classification();
    assertTrue(hintedClassification.hintEligible());
    assertEquals(
        765,
        ClassificationPolicy.cachePriority(hinted, hintedClassification, LexiconHintAction.HINT));
    // 四词词形在发布消费者降为 BLOCK，但仍保留原 prewarm 分数。
    assertEquals(
        765,
        ClassificationPolicy.cachePriority(hinted, hintedClassification, LexiconHintAction.BLOCK));
    var notPrewarmed = row("nebula", "星云", "fixture", "fixture", null, null, false, 0, 765);
    assertTrue(HintPreparation.prepare(notPrewarmed).classification().hintEligible());
    assertEquals(
        0,
        ClassificationPolicy.cachePriority(
            notPrewarmed,
            HintPreparation.prepare(notPrewarmed).classification(),
            LexiconHintAction.HINT));
  }

  @Test
  void rejectsInconsistentRecordTerminals() {
    var valid =
        new ImportClassification(
            false, false, ImportClassification.FrequencyEvidence.UNKNOWN, true, false, "eligible");
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new ImportClassification(
                true,
                false,
                ImportClassification.FrequencyEvidence.UNKNOWN,
                true,
                false,
                "eligible"));
    assertThrows(
        NullPointerException.class,
        () -> new ImportClassification(false, false, null, true, false, "eligible"));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new ImportClassification(
                true,
                false,
                ImportClassification.FrequencyEvidence.UNKNOWN,
                false,
                true,
                "basic_vocabulary"));
    assertThrows(
        IllegalArgumentException.class,
        () -> new PreparedHint(null, null, "rule", List.of(), valid));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new PreparedHint(
                "释义",
                null,
                "rule",
                List.of(),
                new ImportClassification(
                    false,
                    false,
                    ImportClassification.FrequencyEvidence.UNKNOWN,
                    false,
                    false,
                    "blocked")));
  }

  @Test
  void preservesCleanerTraceWhenCandidateIsRejected() {
    var raw = "【医】<专业>术语";
    var row =
        new LexiconImportRow(
            "kidney",
            raw,
            "",
            List.of(),
            List.of(),
            new LexiconPriority(0, 0, 0),
            source("ecdict-stardict"),
            source("ecdict-bnc-frq"),
            List.of(),
            false,
            false,
            "fixture",
            raw,
            null,
            null,
            List.of(),
            false,
            false,
            false);
    var prepared = HintPreparation.prepare(row);
    assertEquals("unsafe_default_candidate", prepared.exclusionReason());
    assertEquals(List.of("source_label"), prepared.matchedRules());
    assertFalse(prepared.classification().hintEligible());
  }

  private static LexiconImportRow row(
      String lemma,
      String gloss,
      String dictionaryId,
      String frequencyId,
      Long bnc,
      Long frq,
      boolean prewarm,
      int complexCount,
      int memoryPriority) {
    var complexSources =
        complexCount == 0 ? List.<SourceReference>of() : List.of(source("ecdict-tag-gk"));
    return new LexiconImportRow(
        lemma,
        gloss,
        "",
        List.of(),
        List.of(),
        new LexiconPriority(0, complexCount, memoryPriority),
        source(dictionaryId),
        source(frequencyId),
        complexSources,
        prewarm,
        false,
        "fixture",
        gloss,
        bnc,
        frq,
        complexCount == 0 ? List.of() : List.of("gk"),
        false,
        false,
        false);
  }

  private static SourceReference source(String id) {
    return new SourceReference(id, "MIT", "fixture");
  }
}
