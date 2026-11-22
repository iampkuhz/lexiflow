package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.enrichment.domain.model.AnnotationHint;
import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import java.util.List;
import org.junit.jupiter.api.Test;

class IncrementalHintMapperTest {
  @Test
  void mapsUtf16HalfOpenOffsetsAcrossAppendSegments() {
    var request =
        new CaptionIncrementalRequest(
            List.of(),
            new CaptionIncrementalRequest.Snapshot(
                List.of(
                    new CaptionIncrementalRequest.Group(
                        List.of(
                            new CaptionIncrementalRequest.Segment("old", "😀 ", false, 0),
                            new CaptionIncrementalRequest.Segment("a", "go", true, 0),
                            new CaptionIncrementalRequest.Segment("b", "al", true, 0))))));
    var plan = new IncrementalCaptionPlan().plan(request).getFirst();
    var annotation =
        new AnnotationHint(3, 7, "1", "00000000-0000-0000-0000-000000000002", 1, "出发", "go", null);
    var hint = new IncrementalHintMapper().map(request, plan, List.of(annotation)).getFirst();
    assertEquals("a", hint.startKey());
    assertEquals(0, hint.startOffset());
    assertEquals("b", hint.endKey());
    assertEquals(2, hint.endOffset());
  }

  @Test
  void mapsExactBoundariesAndOldStartToAppendEnd() {
    var request = request();
    var plan = new IncrementalCaptionPlan().plan(request).getFirst();
    var mapper = new IncrementalHintMapper();
    var boundaryStart = mapper.map(request, plan, List.of(hint(5, 6))).getFirst();
    assertEquals("b", boundaryStart.startKey());
    assertEquals(0, boundaryStart.startOffset());
    var boundaryEnd = mapper.map(request, plan, List.of(hint(3, 5))).getFirst();
    assertEquals("a", boundaryEnd.endKey());
    assertEquals(2, boundaryEnd.endOffset());
    var oldToAppend = mapper.map(request, plan, List.of(hint(2, 4))).getFirst();
    assertEquals("old", oldToAppend.startKey());
    assertEquals("a", oldToAppend.endKey());
  }

  @Test
  void rejectsHintsOutsideContextOrAppendEnd() {
    var request =
        new CaptionIncrementalRequest(
            List.of(),
            new CaptionIncrementalRequest.Snapshot(
                List.of(
                    new CaptionIncrementalRequest.Group(
                        List.of(
                            new CaptionIncrementalRequest.Segment(
                                "too-old", "q".repeat(60), false, 0),
                            new CaptionIncrementalRequest.Segment("tail", "t".repeat(10), false, 0),
                            new CaptionIncrementalRequest.Segment("new", "go", true, 0))))));
    var plan = new IncrementalCaptionPlan().plan(request).getFirst();
    var mapper = new IncrementalHintMapper();
    assertThrows(
        IllegalArgumentException.class, () -> mapper.map(request, plan, List.of(hint(59, 71))));
    assertThrows(
        IllegalArgumentException.class, () -> mapper.map(request, plan, List.of(hint(65, 70))));
    assertThrows(
        IllegalArgumentException.class, () -> mapper.map(request, plan, List.of(hint(70, 73))));
  }

  private static CaptionIncrementalRequest request() {
    return new CaptionIncrementalRequest(
        List.of(),
        new CaptionIncrementalRequest.Snapshot(
            List.of(
                new CaptionIncrementalRequest.Group(
                    List.of(
                        new CaptionIncrementalRequest.Segment("old", "😀 ", false, 0),
                        new CaptionIncrementalRequest.Segment("a", "go", true, 0),
                        new CaptionIncrementalRequest.Segment("b", "al", true, 0))))));
  }

  private static AnnotationHint hint(int start, int end) {
    return new AnnotationHint(
        start, end, "1", "00000000-0000-0000-0000-000000000002", 1, "释义", "goal", null);
  }
}
