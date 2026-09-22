package io.lexiflow.enrichment.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.lexicon.domain.LexiconAlias;
import io.lexiflow.lexicon.domain.LexiconEntry;
import io.lexiflow.lexicon.domain.LexiconEntryKind;
import io.lexiflow.lexicon.domain.LexiconInflection;
import io.lexiflow.lexicon.domain.LexiconProvenance;
import io.lexiflow.lexicon.domain.LexiconSense;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class DeterministicHintPolicyTest {
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  @Test
  void matchesAliasesAndInflectionsAtTheirActualSurfaceOffsets() {
    for (var surface : List.of("dependable", "reliably")) {
      var caption = "It works " + surface + ".";
      var result = policy.evaluate(context(caption, 0, caption.length()), List.of(entry()));
      assertEquals(HintState.READY, result.state());
      var hint = result.hints().getFirst();
      assertEquals(surface, caption.substring(hint.startOffset(), hint.endOffset()));
      assertEquals(entry().entryId().toString(), hint.lexiconEntryId());
    }
  }

  @Test
  void findsStandaloneOccurrenceAfterAnEmbeddedSubstring() {
    var caption = "Unreliable is not reliable.";
    var result = policy.evaluate(context(caption, 0, caption.length()), List.of(entry()));
    assertEquals(18, result.hints().getFirst().startOffset());
  }

  @Test
  void preservesUtf16OffsetsWhenCaseFoldingWouldExpandAnEarlierCharacter() {
    var caption = "İ reliable";
    var result = policy.evaluate(context(caption, 0, caption.length()), List.of(entry()));
    assertEquals(2, result.hints().getFirst().startOffset());
    assertEquals(10, result.hints().getFirst().endOffset());
  }

  @Test
  void doesNotMatchOutsideTargetRangeOrInsideALongerWord() {
    assertEquals(
        HintState.NO_PENDING,
        policy.evaluate(context("unreliable", 0, 10), List.of(entry())).state());
    assertEquals(
        HintState.NO_PENDING, policy.evaluate(context("reliable", 0, 4), List.of(entry())).state());
  }

  private static CaptionContext context(String caption, int start, int end) {
    return new CaptionContext(UUID.randomUUID(), 1, "a".repeat(64), caption, start, end);
  }

  private static LexiconEntry entry() {
    return new LexiconEntry(
        UUID.fromString("00000000-0000-0000-0000-000000000001"),
        1,
        "en",
        LexiconEntryKind.WORD,
        "reliable",
        List.of(new LexiconSense(UUID.randomUUID(), "可靠的", "reliable", "synthetic")),
        List.of(new LexiconAlias("dependable")),
        List.of(new LexiconInflection("reliably")),
        new LexiconProvenance("synthetic", "MIT", "a".repeat(64), Instant.EPOCH));
  }
}
