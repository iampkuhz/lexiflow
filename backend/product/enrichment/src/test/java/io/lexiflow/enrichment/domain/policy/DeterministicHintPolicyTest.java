package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.HintState;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class DeterministicHintPolicyTest {
  private static final String DIGEST = "a".repeat(64);
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  @Test
  void publishedShortPhraseDisplaysAndGlossSafetyAndBlockRemainEnforced() {
    assertEquals(
        HintState.READY,
        evaluate("the silent majority", List.of(phrase("the silent majority", "沉默的大多数"))).state());
    assertEquals(
        HintState.READY, evaluate("the majority", List.of(phrase("the majority", "大多数"))).state());
    assertEquals(
        HintState.NO_PENDING,
        evaluate("the silent majority", List.of(phrase("the silent majority", "在…之中"))).state());
    assertEquals(HintState.NO_PENDING, evaluate("give up", List.of(block("give up"))).state());
  }

  @Test
  void displaysOnlySafeHintUsingProvidedGlossAndSense() {
    var good = candidate("reliable", "可靠的");
    var result = evaluate("A reliable result", List.of(good));
    assertEquals(HintState.READY, result.state());
    assertEquals("可靠的", result.hints().getFirst().chineseGloss());
    assertEquals(good.senseId().toString(), result.hints().getFirst().senseId());
    for (var unsafe : List.of("reliable", "一".repeat(25), "可靠\u0000", "可靠（结果）", "<b>可靠</b>")) {
      assertEquals(
          HintState.NO_PENDING,
          evaluate("reliable", List.of(candidate("reliable", unsafe))).state());
    }
  }

  @Test
  void blockDoesNotDisplayAndStillMakesSameFormAmbiguous() {
    var hint = candidate("bank", "银行");
    var block = block("bank");
    assertEquals(HintState.NO_PENDING, evaluate("bank", List.of(hint, block)).state());
    assertEquals(HintState.NO_PENDING, evaluate("bank", List.of(block)).state());
    var selection =
        new DeterministicHintPolicy().evaluateSelection("bank", 0, 4, 0, List.of(hint, block));
    assertEquals(1, selection.ambiguous());
    assertEquals(0, selection.overlapDropped());
  }

  @Test
  void refusesMixedVersionsAndPreservesUtf16RangesAndWordBoundaries() {
    assertEquals(
        HintState.NO_PENDING,
        evaluate(
                "reliable",
                List.of(
                    candidate("reliable", "可靠的"),
                    candidate("context", "语境", 2, LexiconEntryKind.WORD, 0)))
            .state());
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
            phrase("very reliable", "非常可靠"),
            candidate("reliable", "可靠的"),
            ranked("context", "语境"),
            ranked("metaphor", "隐喻"),
            ranked("literally", "按字面"));
    var result = evaluate(text, candidates);
    assertEquals(4, result.hints().size());
    assertEquals(
        List.of("very reliable", "context", "metaphor", "literally"),
        result.hints().stream().map(h -> text.substring(h.startOffset(), h.endOffset())).toList());
  }

  @Test
  void usesFrozenFinalPriorityForEqualTierMatches() {
    var low = candidate("context", "语境", 1, LexiconEntryKind.WORD, 4.2);
    var highBase = candidate("literally", "按字面意思", 1, LexiconEntryKind.WORD, 4.2);
    var high =
        new LexiconHintCandidate(
            highBase.entryId(),
            highBase.senseId(),
            highBase.lexiconVersion(),
            highBase.languageTag(),
            highBase.normalizedForm(),
            highBase.canonicalLemma(),
            highBase.entryKind(),
            highBase.finalAction(),
            highBase.finalGloss(),
            900,
            highBase.rankedWord(),
            highBase.complexListCount());
    var result =
        evaluate(
            "context metaphor algorithm literally",
            List.of(
                low,
                candidate("metaphor", "隐喻", 1, LexiconEntryKind.WORD, 4.2),
                candidate("algorithm", "算法", 1, LexiconEntryKind.WORD, 4.2),
                high));
    assertEquals(4, result.hints().size());
    assertEquals(
        List.of("语境", "隐喻", "算法", "按字面意思"),
        result.hints().stream().map(hint -> hint.chineseGloss()).toList());
  }

  @Test
  void displaysFirstCandidateFromMultiSenseSourceWithoutSplittingAtViewTime() {
    var firstSense = candidate("bank", "银行");
    var result = evaluate("a bank account", List.of(firstSense));
    assertEquals(HintState.READY, result.state());
    assertEquals("银行", result.hints().getFirst().chineseGloss());
  }

  @Test
  void consumesPublishedLowInformationPhraseWithoutRecomputingSourceQualification() {
    var form = "the first";
    var result = evaluate(form, List.of(phrase(form, "第一")));
    assertEquals(HintState.READY, result.state());
    assertEquals("第一", result.hints().getFirst().chineseGloss());
  }

  @Test
  void unsafeHintStillMakesSameFormAmbiguousWithSafeHint() {
    var safe = candidate("bank", "银行");
    var unsafeBase = candidate("bank", "银行");
    var unsafe =
        new LexiconHintCandidate(
            2L,
            unsafeBase.senseId(),
            1,
            "en",
            "bank",
            "bank",
            LexiconEntryKind.WORD,
            LexiconHintAction.HINT,
            "银行（旧）",
            500,
            false,
            1);
    assertEquals(HintState.NO_PENDING, evaluate("bank", List.of(safe, unsafe)).state());
  }

  @Test
  void nullCandidateRefusesWholeBatchAndRepeatedOccurrencesArePreserved() {
    var safe = candidate("bank", "银行");
    assertEquals(
        HintState.NO_PENDING, evaluate("bank", java.util.Arrays.asList(safe, null)).state());
    var repeated = evaluate("bank, BANK", List.of(safe));
    assertEquals(2, repeated.hints().size());
    assertEquals(0, repeated.hints().getFirst().startOffset());
    assertEquals(4, repeated.hints().getFirst().endOffset());
  }

  @Test
  void oldContextCanCompletePhraseButNeverReemitOldOnlyHint() {
    var text = "bank give up";
    var hints =
        policy.evaluate(
            text, 0, text.length(), 10, List.of(candidate("bank", "银行"), phrase("give up", "放弃")));
    assertEquals(1, hints.size());
    assertEquals(5, hints.getFirst().startOffset());
    assertEquals(12, hints.getFirst().endOffset());
    assertEquals("放弃", hints.getFirst().chineseGloss());
  }

  private io.lexiflow.enrichment.domain.model.CaptionHintResult evaluate(
      String text, List<LexiconHintCandidate> candidates) {
    return policy.evaluate(
        new CaptionContext(UUID.randomUUID(), 1, DIGEST, text, 0, text.length()), candidates);
  }

  private static LexiconHintCandidate candidate(String form, String gloss) {
    return candidate(form, gloss, 1, LexiconEntryKind.WORD, 0);
  }

  private static LexiconHintCandidate ranked(String form, String gloss) {
    return candidate(form, gloss, 1, LexiconEntryKind.WORD, 3.5);
  }

  private static LexiconHintCandidate phrase(String form, String gloss) {
    return candidate(form, gloss, 1, LexiconEntryKind.PHRASE, 0);
  }

  private static LexiconHintCandidate candidate(
      String form, String gloss, long version, LexiconEntryKind kind, double zipf) {
    var id = (long) (form + version).hashCode() & Long.MAX_VALUE;
    if (id == 0) id = 1;
    var senseId = UUID.nameUUIDFromBytes(gloss.getBytes(StandardCharsets.UTF_8));
    return new LexiconHintCandidate(
        id,
        senseId,
        version,
        "en",
        form,
        form,
        kind,
        LexiconHintAction.HINT,
        gloss,
        500,
        zipf > 0,
        1);
  }

  private static LexiconHintCandidate block(String form) {
    var id = (long) form.hashCode() & Long.MAX_VALUE;
    if (id == 0) id = 1;
    return new LexiconHintCandidate(
        id,
        null,
        1,
        "en",
        form,
        form,
        LexiconEntryKind.WORD,
        LexiconHintAction.BLOCK,
        null,
        0,
        false,
        0);
  }
}
