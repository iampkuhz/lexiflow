package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.api.hints.model.CaptionHintResponse;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.web.MockHttpServletRequest;

@SpringBootTest(
    properties = {
      "lexiflow.runtime.mode=demo",
      "lexiflow.segment-analysis.enabled=true",
      "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-caption-test-${random.uuid}.jsonl"
    })
class CaptionHintControllerTest {
  @Autowired private CaptionHintController controller;

  @Test
  void returnsKeyedHintAndProcessesNoHintSegments() {
    var response =
        (CaptionHintResponse)
            controller
                .hint(
                    request(
                        new CaptionHintRequest.Segment("old", "We need ", false, 0L),
                        new CaptionHintRequest.Segment("a", "reli", true, 0L),
                        new CaptionHintRequest.Segment("b", "able", true, 0L),
                        new CaptionHintRequest.Segment("c", " zxqv", true, 0L)),
                    servletRequest())
                .getBody();
    assertEquals(List.of("a", "b", "c"), response.processedKeys());
    assertEquals(1, response.hints().size());
    var hint = response.hints().getFirst();
    assertEquals("a", hint.startKey());
    assertEquals(0, hint.startOffset());
    assertEquals("b", hint.endKey());
    assertEquals(4, hint.endOffset());
    assertEquals("可靠的", hint.chineseGloss());
    assertTrue(response.lexiconVersion() > 0);
  }

  @Test
  void noAppendMeansNoQueriesAndEmptyResult() {
    var response =
        (CaptionHintResponse)
            controller
                .hint(
                    request(new CaptionHintRequest.Segment("old", "reliable", false, 0L)),
                    servletRequest())
                .getBody();
    assertTrue(response.processedKeys().isEmpty());
    assertTrue(response.hints().isEmpty());
  }

  private MockHttpServletRequest servletRequest() {
    var request = new MockHttpServletRequest();
    request.setAttribute(
        CaptionRequestObservation.ATTRIBUTE,
        new CaptionRequestObservation(UUID.randomUUID(), System.nanoTime()));
    return request;
  }

  private static CaptionHintRequest request(CaptionHintRequest.Segment... segments) {
    return new CaptionHintRequest(
        List.of(),
        new CaptionHintRequest.Snapshot(
            List.of(new CaptionHintRequest.CaptionGroup(List.of(segments)))));
  }
}
