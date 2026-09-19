package io.lexiflow.workflow.application;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.CaptionContext;
import io.lexiflow.enrichment.domain.DeterministicHintPolicy;
import io.lexiflow.enrichment.domain.HintState;
import io.lexiflow.lexicon.domain.BuiltinLexiconCatalog;
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
}
