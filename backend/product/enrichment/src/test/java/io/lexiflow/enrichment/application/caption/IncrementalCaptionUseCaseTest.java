package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.ArrayList;
import java.util.List;
import java.util.OptionalLong;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class IncrementalCaptionUseCaseTest {
  @Test
  void queriesOnlyContiguousAppendIntervalsAndMapsCrossSegmentPhrase() {
    var queries = new ArrayList<String>();
    LexiconCatalog catalog =
        forms -> {
          queries.add(String.join(" ", forms));
          return lookup(forms, candidates(String.join(" ", forms)));
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
    var measured = useCase.enrichIncrementalMeasured(request);
    var result = measured.result();
    assertEquals(
        List.of(
            "context context figure context figure out figure figure out out",
            "reliable reliable caption caption"),
        queries);
    assertEquals(List.of("first", "last", "new"), result.processedKeys());
    assertEquals(2, result.hints().size());
    assertEquals("first", result.hints().getFirst().startKey());
    assertEquals(0, result.hints().getFirst().startOffset());
    assertEquals("last", result.hints().getFirst().endKey());
    assertEquals(3, result.hints().getFirst().endOffset());
    assertEquals(2, measured.diagnostics().newRanges());
    assertEquals(2, result.hints().size());
    assertTrue(measured.diagnostics().candidatesNanos() > 0);
    assertTrue(measured.queryNanos() > 0);
    assertTrue(measured.rulesNanos() > 0);
  }

  @Test
  void adjacentOldTailCanCompletePhraseButOtherGroupsCannot() {
    var useCase =
        new EnrichCaptionUseCase(
            forms -> lookup(forms, candidates(String.join(" ", forms))),
            new DeterministicHintPolicy());
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
    LexiconCatalog catalog =
        forms -> {
          queries.add(String.join(" ", forms));
          return lookup(forms, candidates(String.join(" ", forms)));
        };
    var measured =
        new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy())
            .enrichIncrementalMeasured(
                request(
                    group(
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
        forms -> {
          if (forms.contains("figure out"))
            return lookup(forms, List.of(candidate("figure out", "弄明白")));
          if (forms.contains("caption")) {
            var original = candidate("caption", "字幕");
            return lookup(
                forms,
                List.of(
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
                        original.complexListCount())));
          }
          return lookup(forms, List.of());
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
    assertEquals(2, result.queryCounts().versionReads());
  }

  @Test
  void suppressesHintsWhenDifferentVersionWasSeenOnNoHintLookup() {
    LexiconCatalog catalog =
        forms -> {
          if (forms.contains("caption")) {
            var candidate = candidate("caption", "字幕", 2);
            return new LexiconLookupResult(
                List.of(candidate),
                OptionalLong.of(2),
                new LexiconLookupResult.Counts(forms.size(), 1, 0, forms.size() - 1, 1, 1, 0));
          }
          return new LexiconLookupResult(
              List.of(),
              OptionalLong.of(1),
              new LexiconLookupResult.Counts(forms.size(), 0, 0, forms.size(), 1, 1, 0));
        };
    var measured =
        new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy())
            .enrichIncrementalMeasured(
                request(
                    group(segment("a", "ordinary", true)), group(segment("b", "caption", true))));
    assertTrue(measured.result().hints().isEmpty());
    assertEquals(2, measured.queryCounts().versionReads());
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
    assertEquals("可靠的", measured.result().hints().getFirst().chineseGloss());
  }

  @Test
  void deduplicationCannotHideSameEntryWithDifferentVersions() {
    var calls = new java.util.concurrent.atomic.AtomicInteger();
    LexiconCatalog catalog =
        forms ->
            lookup(
                forms,
                forms.contains("caption")
                    ? List.of(candidate("caption", "字幕", calls.incrementAndGet()))
                    : List.of());
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
    assertEquals(2, measured.queryCounts().versionReads());
  }

  @Test
  void hiddenAndUnmatchedCandidatesFromAnotherVersionSuppressRealHintAcrossIntervals() {
    LexiconCatalog catalog =
        forms -> {
          if (forms.contains("caption"))
            return lookup(forms, List.of(candidate("caption", "字幕", 1)));
          var block = blockCandidate("ordinary", 2);
          var unsafe =
              new LexiconHintCandidate(
                  UUID.nameUUIDFromBytes(
                      "unsafe".getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                  UUID.nameUUIDFromBytes(
                      "unsafe-sense".getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                  2,
                  "en",
                  "ordinary",
                  "ordinary",
                  LexiconEntryKind.WORD,
                  LexiconHintAction.HINT,
                  "释".repeat(25),
                  500,
                  0,
                  0);
          var unmatched = candidate("never-present", "隐藏候选", 2);
          return new LexiconLookupResult(
              List.of(block, unsafe, unmatched),
              OptionalLong.empty(),
              new LexiconLookupResult.Counts(forms.size(), 1, 0, forms.size() - 1, 1, 1, 0));
        };
    var measured =
        new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy())
            .enrichIncrementalMeasured(
                request(
                    group(
                        segment("a", "ordinary", true),
                        segment("gap", " ", false),
                        segment("b", "caption", true))));
    assertTrue(measured.result().hints().isEmpty());
    assertEquals(List.of("a", "b"), measured.result().processedKeys());
    assertEquals(4, measured.candidateCount());
    assertEquals(2, measured.queryCounts().versionReads());
    assertTrue(measured.queryNanos() >= 0);
    assertTrue(measured.rulesNanos() >= 0);
  }

  @Test
  void noAppendAndEmptyFormsPerformNoCatalogAccess() {
    var calls = new java.util.concurrent.atomic.AtomicInteger();
    LexiconCatalog catalog =
        forms -> {
          calls.incrementAndGet();
          return lookup(forms, List.of());
        };
    var useCase = new EnrichCaptionUseCase(catalog, new DeterministicHintPolicy());
    var noAppend =
        useCase.enrichIncrementalMeasured(request(group(segment("old", "caption", false))));
    var punctuationOnly =
        useCase.enrichIncrementalMeasured(request(group(segment("punct", "...", true))));
    assertEquals(0, calls.get());
    assertEquals(List.of(), noAppend.result().processedKeys());
    assertEquals(List.of("punct"), punctuationOnly.result().processedKeys());
    assertEquals(0, punctuationOnly.queryCounts().queryKeys());
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

  private static LexiconLookupResult lookup(
      List<String> forms, List<LexiconHintCandidate> candidates) {
    int positives =
        (int)
            forms.stream()
                .filter(form -> candidates.stream().anyMatch(c -> c.normalizedForm().equals(form)))
                .count();
    long version = candidates.isEmpty() ? 1 : candidates.getFirst().lexiconVersion();
    return new LexiconLookupResult(
        candidates,
        OptionalLong.of(version),
        new LexiconLookupResult.Counts(
            forms.size(), positives, forms.size() - positives, 0, 0, 1, 0));
  }

  private static List<LexiconHintCandidate> candidates(String text) {
    var result = new ArrayList<LexiconHintCandidate>();
    if (text.contains("figure out")) result.add(candidate("figure out", "弄明白"));
    if (text.contains("caption")) result.add(candidate("caption", "字幕"));
    if (text.contains("reliable")) {
      result.addAll(new BuiltinLexiconCatalog().lookupForms(List.of("reliable")).candidates());
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

  private static LexiconHintCandidate blockCandidate(String form, long version) {
    return new LexiconHintCandidate(
        UUID.nameUUIDFromBytes(form.getBytes(java.nio.charset.StandardCharsets.UTF_8)),
        null,
        version,
        "en",
        form,
        form,
        LexiconEntryKind.WORD,
        LexiconHintAction.BLOCK,
        null,
        500,
        0,
        0);
  }
}
