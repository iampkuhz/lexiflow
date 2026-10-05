package io.lexiflow.lexicon.application.importing.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

/** 每个范围及资格分流都有正反例；格式成功不替代资格和发布合同。 */
class PreparationPipelineTest {
  @Test
  void rejectsShortStandaloneWordsButPreservesFullPhrasesAndThreeLetterBoundary() {
    for (var word : List.of("a", "I", "UH", "um", "AI", "TV", "'uh'", "um!")) {
      assertFalse(LexiconSurfacePolicy.withinQueryWindow(word), word);
    }
    for (var word : List.of("cat", "uhm", "of course", "in front of")) {
      assertTrue(LexiconSurfacePolicy.withinQueryWindow(word), word);
    }
  }

  @Test
  void separatesNonAsciiLettersFromPunctuation() {
    assertEquals("non_ascii_lemma", result("bünde", "城镇").exclusionReason());
    assertNull(result("star-chart", "星图").exclusionReason());
  }

  @Test
  void defersOverWindowCanonicalButPreservesExistingShortAlias() {
    assertEquals(
        "outside_query_window", result("one distant stellar system", "星系").exclusionReason());
    var aliased =
        row(
            "one distant stellar system",
            "星系",
            "星系",
            false,
            false,
            false,
            null,
            0,
            List.of("quasar"));
    assertNull(HintPreparation.prepare(aliased).exclusionReason());
    assertTrue(LexiconSurfacePolicy.withinQueryWindow("one two three"));
    assertFalse(LexiconSurfacePolicy.withinQueryWindow("one two three four"));
    assertFalse(LexiconSurfacePolicy.withinQueryWindow("123"));
  }

  @Test
  void keepsAllBasicPhraseIndependentFromSingleWordBasic() {
    var phrase = row("bright light", "明亮的光", "明亮的光", false, true, false, null, 0, List.of());
    assertFalse(phrase.basicVocabulary());
    assertEquals("all_basic_phrase", HintPreparation.exclusionReason(phrase));
    assertNull(result("bright quasar", "明亮类星体").exclusionReason());
    var basic = row("direct", "直接的", "直接的", true, false, false, null, 0, List.of());
    assertEquals("basic_vocabulary", HintPreparation.exclusionReason(basic));
  }

  @Test
  void requiresBothLowInformationShapeAndTooFewContentWords() {
    assertTrue(LexiconSurfacePolicy.lowInformationPhrase("the majority"));
    assertFalse(LexiconSurfacePolicy.lowInformationPhrase("the silent majority"));
    assertEquals("沉默的大多数", result("the silent majority", "沉默的大多数").gloss());
    assertEquals(
        "unsafe_default_candidate", result("the silent majority", "在…之中").exclusionReason());
    assertFalse(LexiconSurfacePolicy.lowInformationPhrase("thank you"));
    assertFalse(LexiconSurfacePolicy.lowInformationPhrase("of course"));
    assertTrue(LexiconSurfacePolicy.lowInformationPhrase("as it is"));
  }

  @Test
  void usesRawExpansionAndProtectsRankedTerms() {
    String raw = "[=gamma-glutamyltranspeptidase]γ-谷氨酸转肽酶";
    assertEquals("english_heavy_expansion", result("ggpt", raw).exclusionReason());
    var protectedRow = row("ggpt", raw, "", false, false, false, 100L, 0, List.of());
    assertNotEquals("english_heavy_expansion", HintPreparation.exclusionReason(protectedRow));
    assertNotEquals("english_heavy_expansion", result("mri", "[=magnetic]磁性").exclusionReason());
  }

  @Test
  void requiresChemicalKeywordAndNotationAndMissingFrequencyProtection() {
    assertEquals("specialist_notation", result("enzyme-x", "β-糖甙酶").exclusionReason());
    assertNotEquals("specialist_notation", result("enzyme-x", "β糖甙酶").exclusionReason());
    assertNotEquals("specialist_notation", result("enzyme-x", "A-图像").exclusionReason());
    var ranked = row("enzyme-x", "β-糖甙酶", "", false, false, false, 200L, 0, List.of());
    assertNotEquals("specialist_notation", HintPreparation.exclusionReason(ranked));
    var frequent = row("enzyme-x", "β-糖甙酶", "", false, false, false, null, 3, List.of());
    assertNotEquals("specialist_notation", HintPreparation.exclusionReason(frequent));
  }

  @Test
  void distinguishesNoHanSourceFirstCandidateAndMalformedStructure() {
    assertEquals("no_han_source", result("marker", "sigla").exclusionReason());
    assertEquals("no_han_first_candidate", result("marker", "sigla;标记").exclusionReason());
    assertEquals("empty_first_candidate", result("marker", ";标记").exclusionReason());
    assertEquals("empty_first_candidate", result("marker", "标记;其他(").exclusionReason());
    assertEquals("标记", result("marker", "标记;符号").gloss());
  }

  @Test
  void freezesCleanCandidateBeforeCreatingSenseAndPreservesRawSource() {
    var source = row("test-organ", "[医]甲[床]瘤", "甲[床]瘤", false, false, false, null, 0, List.of());
    var plan = LexiconImportPlan.fromRow(source, 7, "fixture-digest", Instant.EPOCH);
    assertEquals("甲床瘤", plan.prepared().gloss());
    assertEquals(plan.prepared().gloss(), plan.entry().senses().getFirst().chineseGloss());
    assertEquals("[医]甲[床]瘤", plan.row().sourceGloss());
    assertEquals("甲[床]瘤", plan.row().chineseGloss());
  }

  @Test
  void preservesExplicitCuratedGlossButNeverBypassesQualification() {
    var curated = row("synthetic", "原始释义(说明)", "指定短释", false, false, true, null, 0, List.of());
    assertEquals("指定短释", HintPreparation.prepare(curated).gloss());
    var blocked = row("synthetic", "原始释义(说明)", "指定短释", true, false, true, null, 0, List.of());
    assertEquals("basic_vocabulary", HintPreparation.exclusionReason(blocked));
    var unsafe = row("synthetic", "原始释义", "不…安全", false, false, true, null, 0, List.of());
    assertEquals("unsafe_default_candidate", HintPreparation.exclusionReason(unsafe));
  }

  @Test
  void validatesDefaultMeaningBeforeApplyingPhraseHeuristicsAndRestrictsCuratedTrust() {
    var ordinaryAllBasic =
        row("stream of data", "数据流", "数据流", false, true, false, null, 0, List.of());
    assertEquals("all_basic_phrase", HintPreparation.exclusionReason(ordinaryAllBasic));
    var cleanedAllBasic =
        row("stream of data", "【医】数据流", "数据流", false, true, false, null, 0, List.of());
    var rejectedAfterCleaning = HintPreparation.prepare(cleanedAllBasic);
    assertEquals("all_basic_phrase", rejectedAfterCleaning.exclusionReason());
    assertEquals(List.of("source_label"), rejectedAfterCleaning.matchedRules());
    var invalidFirst =
        row("stream of data", ";数据流", ";数据流", false, true, false, null, 0, List.of());
    assertEquals("empty_first_candidate", HintPreparation.exclusionReason(invalidFirst));
    var falselyCurated =
        rowWithSource(
            "the majority", "错误释义", "错误释义", false, false, true, null, 0, List.of(), "fixture");
    assertEquals("low_information_phrase", HintPreparation.exclusionReason(falselyCurated));
    var unsafeCurated =
        row("stream of data", "不…安全", "不…安全", false, true, true, null, 0, List.of());
    assertEquals("unsafe_default_candidate", HintPreparation.exclusionReason(unsafeCurated));
  }

  private static io.lexiflow.lexicon.application.importing.model.PreparedHint result(
      String lemma, String raw) {
    return HintPreparation.prepare(row(lemma, raw, raw, false, false, false, null, 0, List.of()));
  }

  private static LexiconImportRow row(
      String lemma,
      String raw,
      String candidate,
      boolean basic,
      boolean allBasic,
      boolean curated,
      Long rank,
      double zipf,
      List<String> aliases) {
    return rowWithSource(
        lemma, raw, candidate, basic, allBasic, curated, rank, zipf, aliases, "ecdict-stardict");
  }

  private static LexiconImportRow rowWithSource(
      String lemma,
      String raw,
      String candidate,
      boolean basic,
      boolean allBasic,
      boolean curated,
      Long rank,
      double zipf,
      List<String> aliases,
      String sourceId) {
    var source = new SourceReference(sourceId, "MIT", "fixture");
    return new LexiconImportRow(
        lemma,
        candidate,
        "",
        aliases,
        List.of(),
        new LexiconPriority(zipf, 0, 100),
        source,
        source,
        List.of(),
        false,
        basic,
        "fixture-policy",
        raw,
        rank,
        null,
        List.of(),
        false,
        allBasic,
        curated);
  }
}
