package io.lexiflow.lexicon.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class LexiconEntryTest {
  @Test
  void normalizesPhraseAndPreservesVersionedSense() {
    var entry = entry(" Figure   Out ", LexiconEntryKind.PHRASE);

    assertEquals("figure out", entry.lemma());
    assertEquals("释义", entry.senses().getFirst().chineseGloss());
    assertEquals(1, entry.lexiconVersion());
  }

  @Test
  void prioritizesIntermediateFrequencyAndComplexListEvidence() {
    var intermediate = LexiconPriority.fromEvidence(4.3, 2);
    var basic = LexiconPriority.fromEvidence(7.0, 2);
    var rare = LexiconPriority.fromEvidence(1.5, 2);

    assertEquals(939, intermediate.memoryPriority());
    assertEquals(389, basic.memoryPriority());
    assertEquals(389, rare.memoryPriority());
  }

  @Test
  void keepsUnrankedComplexWordsOutOfMemoryPrewarm() {
    var unranked = LexiconPriority.fromRankedEvidence(0.0, 3, false);

    assertEquals(0, unranked.memoryPriority());
    assertEquals(3, unranked.complexListCount());
  }

  @Test
  void rejectsAliasOrInflectionThatReusesLemmaSurface() {
    var sense = new LexiconSense(UUID.randomUUID(), "释义", "定义", "source");
    var provenance = new LexiconProvenance("source", "license", "digest", Instant.EPOCH);

    assertThrows(
        IllegalArgumentException.class,
        () ->
            new LexiconEntry(
                UUID.randomUUID(),
                1,
                "en",
                LexiconEntryKind.WORD,
                "reliable",
                List.of(sense),
                List.of(new LexiconAlias("reliable")),
                List.of(new LexiconInflection("reliably")),
                provenance));
  }

  private static LexiconEntry entry(String lemma, LexiconEntryKind kind) {
    return new LexiconEntry(
        UUID.randomUUID(),
        1,
        "en",
        kind,
        lemma,
        List.of(new LexiconSense(UUID.randomUUID(), "释义", "定义", "source")),
        List.of(),
        List.of(),
        new LexiconProvenance("source", "license", "digest", Instant.EPOCH));
  }
}
