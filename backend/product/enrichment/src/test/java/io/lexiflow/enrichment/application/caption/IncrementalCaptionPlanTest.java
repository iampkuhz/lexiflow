package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import java.util.List;
import org.junit.jupiter.api.Test;

class IncrementalCaptionPlanTest {
  @Test
  void boundsContextByWholeSegmentCountAndUtf16Budget() {
    var request =
        new CaptionIncrementalRequest(
            "topic",
            null,
            null,
            new CaptionIncrementalRequest.Snapshot(
                List.of(
                    new CaptionIncrementalRequest.Group(
                        null,
                        null,
                        List.of(
                            segment("a", "x".repeat(25), false, 0),
                            segment("b", "😀".repeat(12), false, 0),
                            segment("c", "tail", true, 0))))));
    var interval = new IncrementalCaptionPlan().plan(request).getFirst();
    assertEquals(1, interval.contextFirst());
    assertEquals(2, interval.appendFirst());
    assertEquals(49, interval.appendStart());
  }

  @Test
  void includesFortyEightButRejectsWholeSegmentThatWouldReachFortyNine() {
    var included = requestWithOldSegments("a".repeat(24), "b".repeat(24));
    var exact = new IncrementalCaptionPlan().plan(included).getFirst();
    assertEquals(0, exact.contextFirst());
    assertEquals(48, exact.appendStart());
    var rejected = requestWithOldSegments("a".repeat(25), "b".repeat(24));
    var overBudget = new IncrementalCaptionPlan().plan(rejected).getFirst();
    assertEquals(1, overBudget.contextFirst());
    assertEquals(49, overBudget.appendStart());
    var oversized =
        new IncrementalCaptionPlan()
            .plan(requestWithOldSegments("a".repeat(24), "b".repeat(49)))
            .getFirst();
    assertEquals(2, oversized.contextFirst());
    assertEquals(oversized.appendStart(), oversized.contextStart());
  }

  private static CaptionIncrementalRequest requestWithOldSegments(String first, String second) {
    return new CaptionIncrementalRequest(
        "topic",
        null,
        null,
        new CaptionIncrementalRequest.Snapshot(
            List.of(
                new CaptionIncrementalRequest.Group(
                    null,
                    null,
                    List.of(
                        segment("first", first, false, 0),
                        segment("second", second, false, 0),
                        segment("new", "x", true, 0))))));
  }

  @Test
  void separatesVisualLinesAndGroups() {
    var request =
        new CaptionIncrementalRequest(
            "topic",
            null,
            null,
            new CaptionIncrementalRequest.Snapshot(
                List.of(
                    new CaptionIncrementalRequest.Group(
                        null,
                        null,
                        List.of(segment("old", "old", false, 0), segment("new", "new", true, 1))),
                    new CaptionIncrementalRequest.Group(
                        null, null, List.of(segment("other", "x", true, 0))))));
    var intervals = new IncrementalCaptionPlan().plan(request);
    assertEquals(2, intervals.size());
    assertEquals(1, intervals.getFirst().contextFirst());
    assertEquals(1, intervals.getLast().groupIndex());
  }

  @Test
  void includesAtMostTwoWholeOldSegmentsWithinFortyEightUtf16Units() {
    var request =
        new CaptionIncrementalRequest(
            "topic",
            null,
            null,
            new CaptionIncrementalRequest.Snapshot(
                List.of(
                    new CaptionIncrementalRequest.Group(
                        null,
                        null,
                        List.of(
                            segment("one", "a".repeat(16), false, 0),
                                segment("two", "b".repeat(16), false, 0),
                            segment("three", "c".repeat(16), false, 0),
                                segment("new", "x", true, 0))))));
    var interval = new IncrementalCaptionPlan().plan(request).getFirst();
    assertEquals(1, interval.contextFirst());
    assertEquals(3, interval.appendFirst());
    assertEquals(48, interval.appendStart());
  }

  private static CaptionIncrementalRequest.Segment segment(
      String key, String text, boolean append, long line) {
    return new CaptionIncrementalRequest.Segment(key, text, null, append, line);
  }
}
