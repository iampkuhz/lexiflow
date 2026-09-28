package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class IncrementalCaptionUseCaseTest {
  @Test
  void queriesOnlyContiguousAppendIntervalsAndMapsCrossSegmentPhrase() {
    var queries = new ArrayList<String>();
    LexiconCatalog catalog =
        text -> {
          queries.add(text);
          return candidates(text);
        };
    var useCase = new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy());
    var request =
        request(
            group(
                segment("old", "context ", false),
                segment("first", "figure ", true),
                segment("last", "out", true),
                segment("gap", " reliable ", false),
                segment("new", "caption", true)));
    var result = useCase.enrichIncrementalMeasured(request).result();
    assertEquals(List.of("context figure out", " reliable caption"), queries);
    assertEquals(List.of("first", "last", "new"), result.processedKeys());
    assertEquals(2, result.hints().size());
    assertEquals("first", result.hints().getFirst().startKey());
    assertEquals(0, result.hints().getFirst().startOffset());
    assertEquals("last", result.hints().getFirst().endKey());
    assertEquals(3, result.hints().getFirst().endOffset());
  }

  @Test
  void adjacentOldTailCanCompletePhraseButOtherGroupsCannot() {
    var useCase =
        new EnrichCaptionUseCase(
            IncrementalCaptionUseCaseTest::candidates, new DeterministicHintPolicy());
    var request =
        request(
            group(segment("old", "figure ", false), segment("new", "out", true)),
            group(segment("other", "figure ", true)),
            group(segment("last", "out", true)));
    var result = useCase.enrichIncrementalMeasured(request).result();
    assertEquals(List.of("new", "other", "last"), result.processedKeys());
    assertEquals(1, result.hints().size());
    assertEquals("old", result.hints().getFirst().startKey());
    assertEquals("new", result.hints().getFirst().endKey());
  }

  @Test
  void visualRowBoundaryCannotCreateLatePhraseOnSealedRow() {
    var queries = new ArrayList<String>();
    LexiconCatalog catalog = text -> { queries.add(text); return candidates(text); };
    var measured = new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy())
        .enrichIncrementalMeasured(request(group(
            new CaptionIncrementalRequest.Segment("old", "figure ", null, false, 0),
            new CaptionIncrementalRequest.Segment("new", "out", null, true, 1))));
    assertEquals(List.of("out"), queries);
    assertEquals(List.of("new"), measured.result().processedKeys());
    assertTrue(measured.result().hints().isEmpty());
  }

  @Test
  void oldTextSuppliesWordBoundaryAndDoesNotBecomeHint() {
    var useCase =
        new EnrichCaptionUseCase(new BuiltinLexiconCatalog(), new DeterministicHintPolicy());
    var request =
        request(
            group(segment("prefix", "un", false), segment("suffix", "reliable", true)),
            group(segment("four", "reliable reliable reliable reliable", true)));
    var result = useCase.enrichIncrementalMeasured(request).result();
    assertEquals(List.of("suffix", "four"), result.processedKeys());
    assertEquals(1, result.hints().size());
    assertTrue(result.hints().stream().allMatch(hint -> hint.startKey().equals("four")));
  }

  @Test
  void suppressesHintsFromDifferentPublishedVersionsAcrossIntervals() {
    LexiconCatalog catalog =
        text -> {
          if (text.equals("figure out")) return List.of(candidate("figure out", "弄明白"));
          if (text.endsWith("caption")) {
            var original = candidate("caption", "字幕");
            return List.of(
                new LexiconHintCandidate(
                    original.entryId(),
                    original.senseId(),
                    2,
                    original.languageTag(),
                    original.normalizedForm(),
                    original.canonicalLemma(),
                    original.entryKind(),
                    original.finalAction(),
                    original.finalGloss(),
                    original.finalPriority(),
                    original.frequencyZipf(),
                    original.complexListCount()));
          }
          return List.of();
        };
    var result =
        new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy())
            .enrichIncrementalMeasured(
                request(
                    group(
                        segment("a", "figure out", true),
                        segment("old", " ", false),
                        segment("b", "caption", true))));
    assertEquals(List.of("a", "b"), result.result().processedKeys());
    assertTrue(result.result().hints().isEmpty());
    assertEquals(result.processedEnglish(), result.processedWithHints());
  }

  @Test
  void hintsEachEntryOnceAcrossIntervalsAndGroupsWithoutLosingCoverage() {
    var measured =
        new EnrichCaptionUseCase(new BuiltinLexiconCatalog(), new DeterministicHintPolicy())
            .enrichIncrementalMeasured(
                request(
                    group(
                        segment("a", "reliable", true),
                        segment("gap", " ", false),
                        segment("b", "reliable", true)),
                    group(segment("c", "reliable", true))));
    assertEquals(List.of("a", "b", "c"), measured.result().processedKeys());
    assertEquals(1, measured.result().hints().size());
    assertEquals("a", measured.result().hints().getFirst().startKey());
    assertEquals(1, measured.processedWithHints().chars().filter(c -> c == '(').count());
  }

  @Test
  void deduplicationCannotHideSameEntryWithDifferentVersions() {
    var calls = new java.util.concurrent.atomic.AtomicInteger();
    LexiconCatalog catalog = text -> List.of(candidate("caption", "字幕", calls.incrementAndGet()));
    var measured =
        new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy())
            .enrichIncrementalMeasured(
                request(
                    group(
                        segment("a", "caption", true),
                        segment("gap", " ", false),
                        segment("b", "caption", true))));
    assertEquals(List.of("a", "b"), measured.result().processedKeys());
    assertTrue(measured.result().hints().isEmpty());
    assertEquals(measured.processedEnglish(), measured.processedWithHints());
  }

  private static CaptionIncrementalRequest request(CaptionIncrementalRequest.Group... groups) {
    return new CaptionIncrementalRequest(
        "topic", null, null, new CaptionIncrementalRequest.Snapshot(List.of(groups)));
  }

  private static CaptionIncrementalRequest.Group group(
      CaptionIncrementalRequest.Segment... segments) {
    return new CaptionIncrementalRequest.Group(null, null, List.of(segments));
  }

  private static CaptionIncrementalRequest.Segment segment(
      String key, String text, boolean append) {
    return new CaptionIncrementalRequest.Segment(key, text, null, append, 0);
  }

  private static List<LexiconHintCandidate> candidates(String text) {
    var result = new ArrayList<LexiconHintCandidate>();
    if (text.contains("figure out")) result.add(candidate("figure out", "弄明白"));
    if (text.contains("caption")) result.add(candidate("caption", "字幕"));
    if (text.contains("reliable")) {
      result.addAll(new BuiltinLexiconCatalog().candidatesFor("reliable"));
    }
    return result;
  }

  private static LexiconHintCandidate candidate(String form, String gloss) {
    return candidate(form, gloss, 1);
  }

  private static LexiconHintCandidate candidate(String form, String gloss, long version) {
    return new LexiconHintCandidate(
        UUID.nameUUIDFromBytes(form.getBytes(java.nio.charset.StandardCharsets.UTF_8)),
        UUID.nameUUIDFromBytes(gloss.getBytes(java.nio.charset.StandardCharsets.UTF_8)),
        version,
        "en",
        form,
        form,
        form.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        LexiconHintAction.HINT,
        gloss,
        500,
        0,
        0);
  }
}
