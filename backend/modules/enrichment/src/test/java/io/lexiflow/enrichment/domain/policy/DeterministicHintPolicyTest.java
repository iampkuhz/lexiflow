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
  void choosesLongestAndHigherValueNonoverlappingMatchesAndLimitsThree() {
    var text = "very reliable context metaphor literally";
    var candidates =
        List.of(
            phrase("very reliable", "非常可靠"),
            candidate("reliable", "可靠的"),
            ranked("context", "语境"),
            ranked("metaphor", "隐喻"),
            ranked("literally", "按字面"));
    var result = evaluate(text, candidates);
    assertEquals(3, result.hints().size());
    assertEquals(
        List.of("context", "metaphor", "literally"),
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
            highBase.frequencyZipf(),
            highBase.complexListCount());
    var result =
        evaluate(
            "context metaphor algorithm literally",
            List.of(
                low,
                candidate("metaphor", "隐喻", 1, LexiconEntryKind.WORD, 4.2),
                candidate("algorithm", "算法", 1, LexiconEntryKind.WORD, 4.2),
                high));
    assertEquals(3, result.hints().size());
    assertEquals(
        List.of("隐喻", "算法", "按字面意思"),
        result.hints().stream().map(hint -> hint.chineseGloss()).toList());
  }

  @Test
  void rejectsLowInformationPhrases() {
    for (var form : List.of("the first", "not in", "reference to", "on yesterday", "to be")) {
      assertEquals(HintState.NO_PENDING, evaluate(form, List.of(phrase(form, "错误短释"))).state());
    }
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
    var id = UUID.nameUUIDFromBytes((form + version).getBytes(StandardCharsets.UTF_8));
    var senseId = UUID.nameUUIDFromBytes(gloss.getBytes(StandardCharsets.UTF_8));
    return new LexiconHintCandidate(
        id, senseId, version, "en", form, form, kind, LexiconHintAction.HINT, gloss, 500, zipf, 1);
  }

  private static LexiconHintCandidate block(String form) {
    var id = UUID.nameUUIDFromBytes(form.getBytes(StandardCharsets.UTF_8));
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
        0,
        0);
  }
}
