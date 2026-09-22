package io.lexiflow.lexicon.application;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.domain.LexiconPriority;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

class LexiconImportPlanTest {
  private static final String DIGEST = "a".repeat(64);

  @Test
  void keepsNaturalInflectionToLemmaAmbiguityWithinOneChunk() {
    assertDoesNotThrow(
        () ->
            LexiconImportPlan.prepare(
                List.of(
                    row("abdu", List.of(), List.of("abdus")), row("abdus", List.of(), List.of())),
                1,
                DIGEST,
                Instant.EPOCH));
  }

  @Test
  void keepsNaturalInflectionToLemmaAmbiguityAcrossChunks() {
    var canonicalSurfaces = LexiconImportPlan.canonicalSurfaceValidator();

    assertDoesNotThrow(
        () -> {
          LexiconImportPlan.prepareNext(
              row("abdu", List.of(), List.of("abdus")),
              1,
              DIGEST,
              Instant.EPOCH,
              canonicalSurfaces);
          LexiconImportPlan.prepareNext(
              row("abdus", List.of(), List.of()), 1, DIGEST, Instant.EPOCH, canonicalSurfaces);
        });
  }

  @Test
  void rejectsDuplicateLemmaAcrossChunks() {
    var canonicalSurfaces = LexiconImportPlan.canonicalSurfaceValidator();
    LexiconImportPlan.prepareNext(
        row("abdu", List.of(), List.of()), 1, DIGEST, Instant.EPOCH, canonicalSurfaces);

    assertThrows(
        IllegalArgumentException.class,
        () ->
            LexiconImportPlan.prepareNext(
                row("abdu", List.of(), List.of()), 1, DIGEST, Instant.EPOCH, canonicalSurfaces));
  }

  @Test
  void rejectsCanonicalAliasCollisionAcrossChunks() {
    var canonicalSurfaces = LexiconImportPlan.canonicalSurfaceValidator();
    LexiconImportPlan.prepareNext(
        row("abdu", List.of(), List.of()), 1, DIGEST, Instant.EPOCH, canonicalSurfaces);

    assertThrows(
        IllegalArgumentException.class,
        () ->
            LexiconImportPlan.prepareNext(
                row("abdus", List.of("abdu"), List.of()),
                1,
                DIGEST,
                Instant.EPOCH,
                canonicalSurfaces));
  }

  private static LexiconImportRow row(
      String lemma, List<String> aliases, List<String> inflections) {
    var source = new SourceReference("fixture", "MIT", lemma);
    return new LexiconImportRow(
        lemma,
        "释义",
        "definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true);
  }
}
