package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class HintSelectionTest {
  @Test
  void preservesPriorityAndSuppressesIdenticalRangeAmbiguityIncludingHiddenMatch() {
    var lowerPriority = match(0, 6, "1", "低", 1, 1);
    var higherPriority = match(5, 9, "2", "高", 9, 1);
    var hiddenAmbiguity = new HintSelection.CandidateMatch(10, 14, "3", null, 1, null, 1, 99, 1);
    var visibleAmbiguity = match(10, 14, "4", "不应展示", 99, 1);
    var selected =
        HintSelection.select(
            List.of(lowerPriority, higherPriority, hiddenAmbiguity, visibleAmbiguity));
    assertEquals(
        List.of("2"), selected.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
  }

  @Test
  void appliesSameTierTiebreaksAndKeepsSelectionStableForShuffledInput() {
    var priorityWinner = match(0, 6, id(1), "优先级", 9, 1, 1, 1);
    var complexLoser = match(1, 7, id(2), "复杂证据", 8, 1, 9, 1);
    assertWinner(priorityWinner, complexLoser, id(1));

    var complexWinner = match(0, 6, id(3), "复杂证据", 8, 1, 9, 1);
    var complexLoser2 = match(1, 7, id(4), "证据少", 8, 1, 8, 1);
    assertWinner(complexWinner, complexLoser2, id(3));

    var longer = match(0, 7, id(5), "更长", 8, 1, 2, 1);
    var shorter = match(0, 5, id(6), "较短", 8, 1, 2, 1);
    assertWinner(longer, shorter, id(5));

    var earlier = match(0, 5, id(7), "较早", 8, 1, 2, 1);
    var later = match(1, 6, id(8), "较晚", 8, 1, 2, 1);
    assertWinner(earlier, later, id(7));
  }

  @Test
  void preservesDistinctOccurrencesAndReturnsSpatialOrder() {
    var repeated = match(8, 11, id(1), "重复", 8, 1, 1, 1);
    var entryFirst = match(0, 3, id(1), "首次", 8, 1, 1, 1);
    var other = match(4, 7, id(2), "另一词", 8, 1, 1, 1);
    var forward = HintSelection.select(List.of(repeated, other, entryFirst));
    var reversed = HintSelection.select(List.of(entryFirst, other, repeated));
    assertEquals(
        List.of(id(1), id(2), id(1)),
        forward.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
    assertEquals(forward, reversed);
  }

  @Test
  void countsUniqueAmbiguityIncludingBlockedEvidenceAndOnlyFinalOverlapDrops() {
    var hiddenAmbiguity = new HintSelection.CandidateMatch(15, 18, id(3), null, 1, null, 1, 10, 0);
    var visibleAmbiguity = match(15, 18, id(4), "隐藏", 10, 1);
    var result =
        HintSelection.select(
            List.of(
                match(0, 5, id(1), "保留", 10, 1),
                match(3, 7, id(2), "重叠淘汰", 9, 1),
                match(10, 14, id(1), "重复词条不计入重叠", 8, 1),
                hiddenAmbiguity,
                visibleAmbiguity));
    assertEquals(1, result.ambiguous());
    assertEquals(1, result.overlapDropped());
    assertEquals(
        List.of(id(1), id(1)), result.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
  }

  private static void assertWinner(
      HintSelection.CandidateMatch first, HintSelection.CandidateMatch second, String expected) {
    var one = HintSelection.select(List.of(first, second));
    var two = HintSelection.select(List.of(second, first));
    assertEquals(
        List.of(expected), one.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
    assertEquals(one, two);
  }

  private static HintSelection.CandidateMatch match(
      int start, int end, String id, String gloss, int priority, long version) {
    return new HintSelection.CandidateMatch(
        start, end, id, "00000000-0000-0000-0000-000000000009", version, gloss, 1, priority, 0);
  }

  private static HintSelection.CandidateMatch match(
      int start,
      int end,
      String id,
      String gloss,
      int priority,
      int tier,
      int complex,
      long version) {
    return new HintSelection.CandidateMatch(
        start,
        end,
        id,
        "00000000-0000-0000-0000-000000000009",
        version,
        gloss,
        tier,
        priority,
        complex);
  }

  private static String id(int suffix) {
    return Integer.toString(suffix);
  }
}
