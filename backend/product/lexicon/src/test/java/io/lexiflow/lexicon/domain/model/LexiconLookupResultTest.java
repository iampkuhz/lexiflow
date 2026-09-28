package io.lexiflow.lexicon.domain.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.ArrayList;
import java.util.List;
import java.util.OptionalLong;
import org.junit.jupiter.api.Test;

class LexiconLookupResultTest {
  @Test
  void defensivelyCopiesCandidatesAndRejectsNullsAndNegativeVersion() {
    var source = new ArrayList<LexiconHintCandidate>();
    var result =
        new LexiconLookupResult(source, OptionalLong.empty(), LexiconLookupResult.Counts.zero());
    source.add(null);
    assertEquals(List.of(), result.candidates());
    assertThrows(UnsupportedOperationException.class, () -> result.candidates().clear());
    assertThrows(
        NullPointerException.class,
        () ->
            new LexiconLookupResult(null, OptionalLong.empty(), LexiconLookupResult.Counts.zero()));
    assertThrows(
        NullPointerException.class,
        () ->
            new LexiconLookupResult(
                java.util.Arrays.asList((LexiconHintCandidate) null),
                OptionalLong.empty(),
                LexiconLookupResult.Counts.zero()));
    assertThrows(
        NullPointerException.class,
        () -> new LexiconLookupResult(List.of(), null, LexiconLookupResult.Counts.zero()));
    assertThrows(
        NullPointerException.class,
        () -> new LexiconLookupResult(List.of(), OptionalLong.empty(), null));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new LexiconLookupResult(
                List.of(), OptionalLong.of(-1), LexiconLookupResult.Counts.zero()));
  }

  @Test
  void rejectsNegativeOrNonConservingCountsIncludingIntWraparound() {
    assertThrows(
        IllegalArgumentException.class, () -> new LexiconLookupResult.Counts(-1, 0, 0, 0, 0, 0, 0));
    assertThrows(
        IllegalArgumentException.class, () -> new LexiconLookupResult.Counts(1, 1, 1, 0, 0, 0, 0));
    assertThrows(
        IllegalArgumentException.class,
        () -> new LexiconLookupResult.Counts(0, Integer.MAX_VALUE, Integer.MAX_VALUE, 2, 0, 0, 0));
  }

  @Test
  void rejectsOverflowWhileAccumulatingEveryCounter() {
    var maximumMisses =
        new LexiconLookupResult.Counts(Integer.MAX_VALUE, 0, 0, Integer.MAX_VALUE, 0, 0, 0);
    var oneMiss = new LexiconLookupResult.Counts(1, 0, 0, 1, 0, 0, 0);
    assertThrows(ArithmeticException.class, () -> maximumMisses.plus(oneMiss));
    var maximumDbBatches = new LexiconLookupResult.Counts(0, 0, 0, 0, Integer.MAX_VALUE, 0, 0);
    var oneDbBatch = new LexiconLookupResult.Counts(0, 0, 0, 0, 1, 0, 0);
    assertThrows(ArithmeticException.class, () -> maximumDbBatches.plus(oneDbBatch));
    var maximumVersionReads = new LexiconLookupResult.Counts(0, 0, 0, 0, 0, Integer.MAX_VALUE, 0);
    var oneVersionRead = new LexiconLookupResult.Counts(0, 0, 0, 0, 0, 1, 0);
    assertThrows(ArithmeticException.class, () -> maximumVersionReads.plus(oneVersionRead));
    var maximumPrewarmReads = new LexiconLookupResult.Counts(0, 0, 0, 0, 0, 0, Integer.MAX_VALUE);
    var onePrewarmRead = new LexiconLookupResult.Counts(0, 0, 0, 0, 0, 0, 1);
    assertThrows(ArithmeticException.class, () -> maximumPrewarmReads.plus(onePrewarmRead));
    assertThrows(NullPointerException.class, () -> LexiconLookupResult.Counts.zero().plus(null));
  }
}
