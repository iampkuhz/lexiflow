package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.api.hints.model.CaptionHintRequest;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest(
    properties =
        "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-caption-test-${random.uuid}.jsonl")
class CaptionHintControllerTest {
  @Autowired private CaptionHintController controller;

  @Test
  void returnsKeyedHintAndProcessesNoHintSegments() {
    var response =
        controller
            .hint(
                request(
                    new CaptionHintRequest.Segment("old", "We need ", null, false, 0L),
                    new CaptionHintRequest.Segment("a", "reli", null, true, 0L),
                    new CaptionHintRequest.Segment("b", "able", null, true, 0L),
                    new CaptionHintRequest.Segment("c", " zxqv", null, true, 0L)))
            .getBody();
    assertEquals(List.of("a", "b", "c"), response.processedKeys());
    assertEquals(1, response.hints().size());
    var hint = response.hints().getFirst();
    assertEquals("a", hint.startKey());
    assertEquals(0, hint.startOffset());
    assertEquals("b", hint.endKey());
    assertEquals(4, hint.endOffset());
    assertEquals("可靠的", hint.chineseGloss());
    assertTrue(hint.lexiconVersion() > 0);
  }

  @Test
  void noAppendMeansNoQueriesAndEmptyResult() {
    var response =
        controller
            .hint(request(new CaptionHintRequest.Segment("old", "reliable", null, false, 0L)))
            .getBody();
    assertTrue(response.processedKeys().isEmpty());
    assertTrue(response.hints().isEmpty());
  }

  private static CaptionHintRequest request(CaptionHintRequest.Segment... segments) {
    return new CaptionHintRequest(
        "topic",
        null,
        null,
        new CaptionHintRequest.Snapshot(
            List.of(new CaptionHintRequest.CaptionGroup(null, null, List.of(segments)))));
  }
}
