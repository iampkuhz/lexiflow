package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.HintState;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class EnrichCaptionUseCaseTest {
  private final EnrichCaptionUseCase useCase =
      new EnrichCaptionUseCase(new BuiltinLexiconCatalog(), new DeterministicHintPolicy());

  @Test
  void returnsChineseGlossForKnownTermInsideTargetRange() {
    var caption = "We need reliable captions.";

    var result =
        useCase.enrich(
            new CaptionContext(
                UUID.fromString("00000000-0000-0000-0000-000000000001"),
                1,
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                caption,
                0,
                caption.length()));

    assertEquals(HintState.READY, result.state());
    assertEquals("可靠的", result.hints().getFirst().chineseGloss());
    assertEquals(
        "reliable",
        caption.substring(
            result.hints().getFirst().startOffset(), result.hints().getFirst().endOffset()));
  }

  @Test
  void preservesEnglishAndSafelyFallsBackForUnknownTerm() {
    var caption = "A quasar crossed the horizon.";

    var result =
        useCase.enrich(
            new CaptionContext(
                UUID.fromString("00000000-0000-0000-0000-000000000001"),
                1,
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                caption,
                0,
                caption.length()));

    assertEquals(HintState.NO_PENDING, result.state());
    assertEquals(caption, result.caption());
    assertEquals(0, result.hints().size());
  }

  @Test
  void measuresQueryAndRulesUsingOneMonotonicClock() {
    var ticks = new java.util.ArrayDeque<>(java.util.List.of(100L, 160L, 175L));
    var measuredUseCase =
        new EnrichCaptionUseCase(
            new BuiltinLexiconCatalog(), new DeterministicHintPolicy(), ticks::removeFirst);
    var measured =
        measuredUseCase.enrichMeasured(
            new CaptionContext(
                UUID.fromString("00000000-0000-0000-0000-000000000001"),
                1,
                "a".repeat(64),
                "reliable",
                0,
                8));
    assertEquals(60, measured.queryNanos());
    assertEquals(15, measured.rulesNanos());
    assertEquals(1, measured.candidateCount());
    assertEquals(HintState.READY, measured.result().state());
    assertEquals(0, ticks.size());
  }
}
