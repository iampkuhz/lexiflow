package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class DeterministicHintPolicyTest {
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  @Test
  void publishedShortPhraseDisplaysAndGlossSafetyAndBlockRemainEnforced() {
    assertEquals(
        false,
        evaluate("the silent majority", List.of(candidate("the silent majority", "沉默的大多数")))
            .hints()
            .isEmpty());
    assertEquals(
        false,
        evaluate("the majority", List.of(candidate("the majority", "大多数"))).hints().isEmpty());
    assertEquals(
        true,
        evaluate("the silent majority", List.of(candidate("the silent majority", "在…之中")))
            .hints()
            .isEmpty());
    assertEquals(true, evaluate("give up", List.of(block("give up"))).hints().isEmpty());
  }

  @Test
  void displaysOnlySafeHintUsingProvidedGlossAndSense() {
    var good = candidate("reliable", "可靠的");
    var result = evaluate("A reliable result", List.of(good));
    assertEquals(false, result.hints().isEmpty());
    assertEquals("可靠的", result.hints().getFirst().chineseGloss());
    assertEquals(good.senseId().toString(), result.hints().getFirst().senseId());
    for (var unsafe : List.of("reliable", "一".repeat(25), "可靠\u0000", "可靠（结果）", "<b>可靠</b>")) {
      assertEquals(
          true, evaluate("reliable", List.of(candidate("reliable", unsafe))).hints().isEmpty());
    }
  }

  @Test
  void carriesExactMatchedAliasSurfaceWithoutCanonicalLemma() {
    var alias =
        new LexiconHintCandidate(
            47,
            UUID.nameUUIDFromBytes("sense".getBytes(StandardCharsets.UTF_8)),
            1,
            "reliables",
            LexiconHintAction.HINT,
            "可靠的",
            500);
    var hint = evaluate("reliables", List.of(alias)).hints().getFirst();
    assertEquals("reliables", hint.lemma());
  }

  @Test
  void blockDoesNotDisplayAndStillMakesAmericanizesAmbiguous() {
    var hint = candidate("americanizes", "使美国化");
    var block = block("americanizes");
    assertEquals(true, evaluate("americanizes", List.of(hint, block)).hints().isEmpty());
    assertEquals(true, evaluate("americanizes", List.of(block)).hints().isEmpty());
    var selection =
        new DeterministicHintPolicy()
            .evaluateSelection("americanizes", 0, 12, 0, List.of(hint, block));
    assertEquals(1, selection.ambiguous());
    assertEquals(0, selection.overlapDropped());
  }

  @Test
  void refusesMixedVersionsAndPreservesUtf16RangesAndWordBoundaries() {
    assertEquals(
        true,
        evaluate(
                "reliable",
                List.of(candidate("reliable", "可靠的"), candidate("context", "语境", 2, 500)))
            .hints()
            .isEmpty());
    var text = "🤖 unreliable reliable_test reliable";
    var result = evaluate(text, List.of(candidate("reliable", "可靠的")));
    var hint = result.hints().getFirst();
    assertEquals(28, hint.startOffset());
    assertEquals(36, hint.endOffset());
    assertEquals("reliable", text.substring(hint.startOffset(), hint.endOffset()));
  }

  @Test
  void choosesLongestAndHigherValueNonoverlappingMatchesWithoutCountLimit() {
    var text = "very reliable context metaphor literally";
    var candidates =
        List.of(
            candidate("very reliable", "非常可靠"),
            candidate("reliable", "可靠的"),
            candidate("context", "语境"),
            candidate("metaphor", "隐喻"),
            candidate("literally", "按字面"));
    var result = evaluate(text, candidates);
    assertEquals(4, result.hints().size());
    assertEquals(
        List.of("very reliable", "context", "metaphor", "literally"),
        result.hints().stream().map(h -> text.substring(h.startOffset(), h.endOffset())).toList());
  }

  @Test
  void usesUnifiedPriorityBeforeLengthForOverlappingMatches() {
    var low = candidate("reliable", "可靠的");
    var high = candidate("very reliable", "高优先级完整短语", 1, 900);
    var result = evaluate("very reliable", List.of(low, high));
    assertEquals(1, result.hints().size());
    assertEquals("高优先级完整短语", result.hints().getFirst().chineseGloss());
  }

  @Test
  void displaysFirstCandidateFromMultiSenseSourceWithoutSplittingAtViewTime() {
    var firstSense = candidate("bank", "银行");
    var result = evaluate("a bank account", List.of(firstSense));
    assertEquals(false, result.hints().isEmpty());
    assertEquals("银行", result.hints().getFirst().chineseGloss());
  }

  @Test
  void consumesPublishedLowInformationPhraseWithoutRecomputingSourceQualification() {
    var form = "the first";
    var result = evaluate(form, List.of(candidate(form, "第一")));
    assertEquals(false, result.hints().isEmpty());
    assertEquals("第一", result.hints().getFirst().chineseGloss());
  }

  @Test
  void unsafeHintStillMakesSameFormAmbiguousWithSafeHint() {
    var safe = candidate("bank", "银行");
    var unsafeBase = candidate("bank", "银行");
    var unsafe =
        new LexiconHintCandidate(
            2L, unsafeBase.senseId(), 1, "bank", LexiconHintAction.HINT, "银行（旧）", 500, null);
    assertEquals(true, evaluate("bank", List.of(safe, unsafe)).hints().isEmpty());
  }

  @Test
  void nullCandidateRefusesWholeBatchAndRepeatedOccurrencesArePreserved() {
    var safe = candidate("bank", "银行");
    assertEquals(true, evaluate("bank", java.util.Arrays.asList(safe, null)).hints().isEmpty());
    var repeated = evaluate("bank, BANK", List.of(safe));
    assertEquals(2, repeated.hints().size());
    assertEquals(0, repeated.hints().getFirst().startOffset());
    assertEquals(4, repeated.hints().getFirst().endOffset());
  }

  @Test
  void oldContextCanCompletePhraseButNeverReemitOldOnlyHint() {
    var text = "bank give up";
    var hints =
        policy
            .evaluateSelection(
                text,
                0,
                text.length(),
                10,
                List.of(candidate("bank", "银行"), candidate("give up", "放弃")))
            .hints();
    assertEquals(1, hints.size());
    assertEquals(5, hints.getFirst().startOffset());
    assertEquals(12, hints.getFirst().endOffset());
    assertEquals("放弃", hints.getFirst().chineseGloss());
  }

  private io.lexiflow.enrichment.domain.model.HintSelectionResult evaluate(
      String text, List<LexiconHintCandidate> candidates) {
    return policy.evaluateSelection(text, 0, text.length(), 0, candidates);
  }

  private static LexiconHintCandidate candidate(String form, String gloss) {
    return candidate(form, gloss, 1, 500);
  }

  private static LexiconHintCandidate candidate(
      String form, String gloss, long version, int priority) {
    var id = (long) (form + version).hashCode() & Long.MAX_VALUE;
    if (id == 0) id = 1;
    var senseId = UUID.nameUUIDFromBytes(gloss.getBytes(StandardCharsets.UTF_8));
    return new LexiconHintCandidate(
        id, senseId, version, form, LexiconHintAction.HINT, gloss, priority, null);
  }

  private static LexiconHintCandidate block(String form) {
    var id = (long) form.hashCode() & Long.MAX_VALUE;
    if (id == 0) id = 1;
    return new LexiconHintCandidate(id, null, 1, form, LexiconHintAction.BLOCK, null, 0, null);
  }
}
