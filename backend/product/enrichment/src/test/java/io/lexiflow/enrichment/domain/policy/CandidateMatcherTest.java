package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class CandidateMatcherTest {
  @Test
  void regionEdgesNeverCreateWordBoundariesAndIncompleteSurfaceDoesNotMatch() {
    assertEquals(List.of(), CandidateMatcher.locate("xbank!", 1, 5, "bank"));
    assertEquals(List.of(), CandidateMatcher.locate("bankx", 0, 4, "bank"));
    assertEquals(List.of(), CandidateMatcher.locate("bank!", 0, 3, "bank"));
    assertEquals(List.of(), CandidateMatcher.locate("bank!", 2, 4, "bank"));
    assertEquals(List.of(), CandidateMatcher.locate("bank!", 0, 0, "bank"));
  }

  @Test
  void supplementaryLettersUseUtf16OffsetsAndCannotMatchHalfOfACodePoint() {
    var upper = "\uD801\uDC00";
    var lower = "\uD801\uDC28";
    var caption = "🤖 " + upper + "!";
    assertEquals(
        List.of(new CandidateMatcher.Match(3, 5)),
        CandidateMatcher.locate(caption, 0, caption.length(), lower));
    assertEquals(List.of(), CandidateMatcher.locate(caption, 3, 4, lower));
    assertEquals(List.of(), CandidateMatcher.locate(caption, 4, 5, "\uDC00"));
    assertEquals(List.of(), CandidateMatcher.locate(upper + "bank", 2, 6, "bank"));
  }

  @Test
  void quotesSurfaceInsteadOfTreatingItAsPatternAndReturnsImmutableOccurrences() {
    var matches = CandidateMatcher.locate("c++ C++ ccc", 0, 11, "c++");
    assertEquals(
        List.of(new CandidateMatcher.Match(0, 3), new CandidateMatcher.Match(4, 7)), matches);
    assertThrows(
        UnsupportedOperationException.class, () -> matches.add(new CandidateMatcher.Match(8, 11)));
  }

  @Test
  void locatesEveryCaseInsensitiveQuotedOccurrenceInsideRangeOnly() {
    var caption = "bank, BANK bank!";
    assertEquals(
        List.of(new CandidateMatcher.Match(0, 4), new CandidateMatcher.Match(6, 10)),
        CandidateMatcher.locate(caption, 0, 10, "bank"));
  }

  @Test
  void preservesOriginalBoundaryAcrossRangeAndDoesNotSplitCombiningOrSupplementaryCodePoints() {
    var caption = "🤖 bank bank\u0301 _bank bank_";
    assertEquals(
        List.of(new CandidateMatcher.Match(3, 7)), CandidateMatcher.locate(caption, 3, 7, "bank"));
    assertEquals(
        List.of(new CandidateMatcher.Match(0, 4)), CandidateMatcher.locate("bank🤖", 0, 5, "bank"));
    var marked = "xbank bank_ bank\u0301";
    assertEquals(List.of(), CandidateMatcher.locate(marked, 0, marked.length(), "bank"));
  }

  @Test
  void internalApostrophesDoNotSplitContractionsButOutsideQuotesRemainBoundaries() {
    for (var caption : List.of("aren’t", "aren't", "aren‘t", "bank’s", "x'bank")) {
      for (var fragment : List.of("aren", "t", "bank")) {
        assertEquals(List.of(), CandidateMatcher.locate(caption, 0, caption.length(), fragment));
      }
    }
    for (var caption : List.of("'bank'", "‘bank’")) {
      var start = caption.indexOf("bank");
      assertEquals(
          List.of(new CandidateMatcher.Match(start, start + 4)),
          CandidateMatcher.locate(caption, 0, caption.length(), "bank"));
    }
    assertEquals(
        List.of(new CandidateMatcher.Match(0, 6)),
        CandidateMatcher.locate("aren’t", 0, 6, "aren’t"));
    assertEquals(List.of(), CandidateMatcher.locate("aren’t", 0, 4, "aren"));
  }

  @Test
  void completeHyphenatedWordsKeepTheirOwnMeaningAndUtf16Offsets() {
    for (var hyphen : List.of("-", "‐", "‑", "－")) {
      String word = "non" + hyphen + "stick";
      String caption = "🤖 ‘" + word + "’ stick, stick!";
      assertEquals(
          List.of(new CandidateMatcher.Match(4, 13)),
          CandidateMatcher.locate(caption, 0, caption.length(), "non-stick"));
      assertEquals(
          List.of(new CandidateMatcher.Match(15, 20), new CandidateMatcher.Match(22, 27)),
          CandidateMatcher.locate(caption, 0, caption.length(), "stick"));
      assertEquals(List.of(), CandidateMatcher.locate(word, 0, word.length(), "stick"));
    }
    assertEquals(
        List.of(new CandidateMatcher.Match(0, 6)),
        CandidateMatcher.locate("that’s", 0, 6, "that's"));
    assertEquals(List.of(), CandidateMatcher.locate("bank-ship", 0, 9, "bank"));
    assertEquals(
        List.of(new CandidateMatcher.Match(0, 5)),
        CandidateMatcher.locate("stick—pan", 0, 9, "stick"));
  }
}
