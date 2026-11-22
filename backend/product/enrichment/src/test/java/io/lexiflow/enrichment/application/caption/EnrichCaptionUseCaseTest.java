package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.List;
import java.util.OptionalLong;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class EnrichCaptionUseCaseTest {
  private final EnrichCaptionUseCase useCase =
      new EnrichCaptionUseCase(catalog(), new DeterministicHintPolicy());

  private static LexiconCatalog catalog() {
    var candidate =
        new LexiconHintCandidate(
            10L,
            UUID.fromString("00000000-0000-0000-0000-000000000011"),
            1,
            "reliable",
            LexiconHintAction.HINT,
            "可靠的",
            500,
            null);
    return forms ->
        new LexiconLookupResult(
            forms.contains("reliable") ? List.of(candidate) : List.of(),
            OptionalLong.of(1),
            new LexiconLookupResult.Counts(forms.size(), 0, 0, forms.size(), 0, 1, 0));
  }

  @Test
  void returnsChineseGlossForKnownTermInsideTargetRange() {
    var caption = "We need reliable captions.";

    var result = useCase.enrichIncrementalMeasured(request(caption)).result();

    assertEquals(List.of("a"), result.processedKeys());
    assertEquals("可靠的", result.hints().getFirst().chineseGloss());
    assertEquals(
        "reliable",
        caption.substring(
            result.hints().getFirst().startOffset(), result.hints().getFirst().endOffset()));
  }

  @Test
  void preservesEnglishAndSafelyFallsBackForUnknownTerm() {
    var caption = "A quasar crossed the horizon.";

    var result = useCase.enrichIncrementalMeasured(request(caption)).result();

    assertEquals(List.of("a"), result.processedKeys());
    assertEquals(0, result.hints().size());
  }

  @Test
  void measuresQueryAndRulesUsingOneMonotonicClock() {
    var ticks =
        new java.util.ArrayDeque<>(java.util.List.of(100L, 105L, 105L, 108L, 110L, 170L, 185L));
    var measuredUseCase =
        new EnrichCaptionUseCase(catalog(), new DeterministicHintPolicy(), ticks::removeFirst);
    var measured = measuredUseCase.enrichIncrementalMeasured(request("reliable"));
    assertEquals(60, measured.queryNanos());
    assertEquals(15, measured.rulesNanos());
    assertEquals(1, measured.candidateCount());
    assertEquals(List.of("a"), measured.result().processedKeys());
    assertEquals(0, ticks.size());
  }

  private static CaptionIncrementalRequest request(String caption) {
    return new CaptionIncrementalRequest(
        List.of(),
        new CaptionIncrementalRequest.Snapshot(
            List.of(
                new CaptionIncrementalRequest.Group(
                    List.of(new CaptionIncrementalRequest.Segment("a", caption, true, 0))))));
  }
}
