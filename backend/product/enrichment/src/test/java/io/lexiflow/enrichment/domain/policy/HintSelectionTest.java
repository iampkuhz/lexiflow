package io.lexiflow.enrichment.domain.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class HintSelectionTest {
  @Test
  void preservesPriorityAndSuppressesIdenticalRangeAmbiguityIncludingHiddenMatch() {
    var lowerPriority = match(0, 6, "1", "低", 1, 1);
    var higherPriority = match(5, 9, "2", "高", 9, 1);
    var hiddenAmbiguity =
        new HintSelection.CandidateMatch(10, 14, "3", null, 1, null, 99, null, null, false, null);
    var visibleAmbiguity = match(10, 14, "4", "不应展示", 99, 1);
    var selected =
        HintSelection.select(
            List.of(lowerPriority, higherPriority, hiddenAmbiguity, visibleAmbiguity), 1500);
    assertEquals(
        List.of("2"), selected.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
  }

  @Test
  void appliesPriorityThenDeterministicTieBreaksIndependentOfInputOrder() {
    var longer = match(0, 7, id(5), "更长", 8, 1);
    var shorter = match(0, 5, id(6), "较短", 8, 1);
    assertWinner(longer, shorter, id(5));

    var earlier = match(0, 5, id(7), "较早", 8, 1);
    var later = match(1, 6, id(8), "较晚", 8, 1);
    assertWinner(earlier, later, id(7));
  }

  @Test
  void preservesDistinctOccurrencesAndReturnsSpatialOrder() {
    var repeated = match(8, 11, id(1), "重复", 8, 1);
    var entryFirst = match(0, 3, id(1), "首次", 8, 1);
    var other = match(4, 7, id(2), "另一词", 8, 1);
    var forward = HintSelection.select(List.of(repeated, other, entryFirst), 1500);
    var reversed = HintSelection.select(List.of(entryFirst, other, repeated), 1500);
    assertEquals(
        List.of(id(1), id(2), id(1)),
        forward.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
    assertEquals(forward, reversed);
  }

  @Test
  void preservesAmbiguityBeforeApplyingCommonWordCutoff() {
    var overCutoff =
        new HintSelection.CandidateMatch(
            0,
            4,
            id(9),
            "00000000-0000-0000-0000-000000000009",
            1,
            "甲",
            10,
            "alpha",
            1200L,
            false,
            null);
    var other = match(0, 4, id(10), "乙", 9, 1);
    var result = HintSelection.select(List.of(overCutoff, other), 1500);
    assertEquals(1, result.ambiguous());
    assertEquals(List.of(), result.hints());
  }

  @Test
  void refusesGroundHomographsEvenForActualGrindingContextsBeforeCutoffOrBlockFiltering() {
    for (var text : List.of("They ground beans.", "freshly ground pepper", "on the ground")) {
      int start = text.indexOf("ground");
      var grind =
          new HintSelection.CandidateMatch(
              start,
              start + 6,
              "3742960728355627465",
              "00000000-0000-0000-0000-000000000001",
              1,
              "磨",
              988,
              "grind",
              5538L,
              false,
              null);
      for (var blocked : List.of(false, true)) {
        var ground =
            new HintSelection.CandidateMatch(
                start,
                start + 6,
                "2532309678823797718",
                blocked ? null : "00000000-0000-0000-0000-000000000002",
                1,
                blocked ? null : "土地",
                498,
                "ground",
                455L,
                false,
                null);
        for (var cutoff : List.of(1000, 1500, 2000)) {
          var result = HintSelection.select(List.of(ground, grind), cutoff);
          assertEquals(1, result.ambiguous());
          // 无提示只是歧义拒绝，不表示选对了研磨或地面语境义。
          assertEquals(List.of(), result.hints());
          assertEquals(result, HintSelection.select(List.of(grind, ground), cutoff));
        }
      }
    }
  }

  @Test
  void preservesUnambiguousBareGrindAndGrinding() {
    for (var form : List.of("grind", "grinding")) {
      var grind =
          new HintSelection.CandidateMatch(
              0,
              form.length(),
              "3742960728355627465",
              "00000000-0000-0000-0000-000000000001",
              1,
              "磨",
              988,
              "grind",
              5538L,
              false,
              null);
      var result = HintSelection.select(List.of(grind), 1500);
      assertEquals(0, result.ambiguous());
      assertEquals(
          List.of("3742960728355627465"),
          result.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
      assertEquals("磨", result.hints().getFirst().chineseGloss());
    }
  }

  @Test
  void cutoffUsesRawRankForEveryWordAndLeavesPhrasesIndependent() {
    var rankedWord =
        new HintSelection.CandidateMatch(
            0,
            5,
            id(11),
            "00000000-0000-0000-0000-000000000009",
            1,
            "释义",
            10,
            "alpha",
            1200L,
            false,
            8L);
    assertEquals(
        List.of("11"),
        HintSelection.select(List.of(rankedWord), 1000).hints().stream()
            .map(hint -> hint.lexiconEntryId())
            .toList());
    assertEquals(
        8L, HintSelection.select(List.of(rankedWord), 1000).hints().getFirst().frequencyRank());
    assertEquals(List.of(), HintSelection.select(List.of(rankedWord), 1500).hints());
    var commonWord =
        new HintSelection.CandidateMatch(
            0,
            5,
            id(12),
            "00000000-0000-0000-0000-000000000009",
            1,
            "释义",
            10,
            "beta",
            900L,
            false,
            null);
    assertEquals(List.of(), HintSelection.select(List.of(commonWord), 1000).hints());
    var phrase =
        new HintSelection.CandidateMatch(
            0,
            5,
            id(13),
            "00000000-0000-0000-0000-000000000009",
            1,
            "释义",
            10,
            "alpha beta",
            100L,
            true,
            null);
    assertEquals(
        List.of("13"),
        HintSelection.select(List.of(phrase), 1000).hints().stream()
            .map(hint -> hint.lexiconEntryId())
            .toList());
  }

  @Test
  void countsUniqueAmbiguityIncludingBlockedEvidenceAndOnlyFinalOverlapDrops() {
    var hiddenAmbiguity =
        new HintSelection.CandidateMatch(15, 18, id(3), null, 1, null, 10, null, null, false, null);
    var visibleAmbiguity = match(15, 18, id(4), "隐藏", 10, 1);
    var result =
        HintSelection.select(
            List.of(
                match(0, 5, id(1), "保留", 10, 1),
                match(3, 7, id(2), "重叠淘汰", 9, 1),
                match(10, 14, id(1), "重复词条不计入重叠", 8, 1),
                hiddenAmbiguity,
                visibleAmbiguity),
            1500);
    assertEquals(1, result.ambiguous());
    assertEquals(1, result.overlapDropped());
    assertEquals(
        List.of(id(1), id(1)), result.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
  }

  private static void assertWinner(
      HintSelection.CandidateMatch first, HintSelection.CandidateMatch second, String expected) {
    var one = HintSelection.select(List.of(first, second), 1500);
    var two = HintSelection.select(List.of(second, first), 1500);
    assertEquals(
        List.of(expected), one.hints().stream().map(hint -> hint.lexiconEntryId()).toList());
    assertEquals(one, two);
  }

  private static HintSelection.CandidateMatch match(
      int start, int end, String id, String gloss, int priority, long version) {
    return new HintSelection.CandidateMatch(
        start,
        end,
        id,
        "00000000-0000-0000-0000-000000000009",
        version,
        gloss,
        priority,
        "fixture",
        null,
        false,
        null);
  }

  private static String id(int suffix) {
    return Integer.toString(suffix);
  }
}
