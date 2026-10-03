package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import java.util.List;
import java.util.OptionalLong;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class IncrementalResultAssemblerTest {
  @Test
  void unknownWithoutCandidatesIsNeutralButKnownZeroConflictsWithRealVersionedHint() {
    var hint = hint("entry-one", 1);
    var unknown = range(OptionalLong.empty(), List.of(), List.of());
    var knownZero = range(OptionalLong.of(0), List.of(), List.of());
    var neutral =
        new IncrementalResultAssembler().assemble(List.of("k"), List.of(unknown, knownZero), 0);
    assertEquals(List.of(), neutral.result().hints());
    assertEquals(OptionalLong.of(0), neutral.diagnostics().publishedVersion());
    var unknownOnly = new IncrementalResultAssembler().assemble(List.of("k"), List.of(unknown), 0);
    assertEquals(OptionalLong.empty(), unknownOnly.diagnostics().publishedVersion());
    var inconsistent = range(OptionalLong.empty(), List.of(safe("entry-one", 1)), List.of(hint));
    assertEquals(
        List.of(hint),
        new IncrementalResultAssembler()
            .assemble(List.of("k"), List.of(unknown, inconsistent), 0)
            .result()
            .hints());
    var conflict =
        new IncrementalResultAssembler()
            .assemble(List.of("k"), List.of(knownZero, inconsistent), 0);
    assertEquals(List.of(), conflict.result().hints());
    assertEquals(true, conflict.diagnostics().versionConflict());
    assertEquals(OptionalLong.empty(), conflict.diagnostics().publishedVersion());
  }

  @Test
  void hiddenCandidatesAndDuplicateEntryVersionsCannotHideMixedVersionEvidence() {
    var hiddenBlock = candidate("blocked-entry", "blocker", 2, LexiconHintAction.BLOCK, null, null);
    var unmatched =
        candidate("unmatched-entry", "unmatched", 2, LexiconHintAction.HINT, "sense-x", "释义");
    var safe = safe("shown-entry", 1);
    var versionTwo = range(OptionalLong.empty(), List.of(hiddenBlock, unmatched), List.of());
    var versionOne = range(OptionalLong.of(1), List.of(safe), List.of(hint("shown-entry", 1)));
    var result =
        new IncrementalResultAssembler()
            .assemble(List.of("a", "b"), List.of(versionTwo, versionOne), 0);
    assertEquals(List.of(), result.result().hints());
    assertEquals(List.of("a", "b"), result.result().processedKeys());
    assertEquals(3, result.candidateCount());
    assertEquals(4, result.queryNanos());
    assertEquals(6, result.rulesNanos());
    assertEquals(2, result.queryCounts().versionReads());
    assertEquals(true, result.diagnostics().versionConflict());
    assertEquals(2, result.diagnostics().newRanges());
  }

  @Test
  void sameEntryAtTwoVersionsDoesNotSurviveDeduplication() {
    var first =
        range(OptionalLong.of(1), List.of(safe("same-entry", 1)), List.of(hint("same-entry", 1)));
    var second =
        range(OptionalLong.of(2), List.of(safe("same-entry", 2)), List.of(hint("same-entry", 2)));
    var result =
        new IncrementalResultAssembler().assemble(List.of("a", "b"), List.of(first, second), 1);
    assertEquals(List.of(), result.result().hints());
    assertEquals(2, result.candidateCount());
    assertEquals(9, result.diagnostics().candidatesNanos());
  }

  private static IncrementalResultAssembler.RangeResult range(
      OptionalLong published,
      List<LexiconHintCandidate> candidates,
      List<IncrementalHintResult.Hint> hints) {
    var lookup =
        new LexiconLookupResult(
            candidates, published, new LexiconLookupResult.Counts(1, 0, 0, 1, 1, 1, 0));
    return new IncrementalResultAssembler.RangeResult(lookup, hints, 2, 3, 0, 0, 4);
  }

  private static LexiconHintCandidate safe(String entry, long version) {
    return candidate(entry, "example", version, LexiconHintAction.HINT, "sense-" + entry, "释义");
  }

  private static LexiconHintCandidate candidate(
      String entry,
      String form,
      long version,
      LexiconHintAction action,
      String sense,
      String gloss) {
    return new LexiconHintCandidate(
        uuid(entry),
        sense == null ? null : uuid(sense),
        version,
        "en",
        form,
        form,
        LexiconEntryKind.WORD,
        action,
        gloss,
        500,
        0,
        0);
  }

  private static IncrementalHintResult.Hint hint(String entry, long version) {
    return new IncrementalHintResult.Hint(
        "k", 0, "k", 1, "释义", uuid(entry).toString(), version, uuid("sense-" + entry).toString());
  }

  private static UUID uuid(String value) {
    return UUID.nameUUIDFromBytes(value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
  }
}
