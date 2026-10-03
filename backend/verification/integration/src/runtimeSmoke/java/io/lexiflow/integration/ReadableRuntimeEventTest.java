package io.lexiflow.integration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 固定可读事件格式的测试读取器不丢失零计数、版本或回滚原因。 */
class ReadableRuntimeEventTest {
  @Test
  void readsLatestEventFieldsWithoutParsingTheBodyOrFrameworkNoise() {
    var id = UUID.randomUUID();
    var line =
        "10-04 01:02:03|INFO|"
            + id
            + "|caption.request.completed|count_cache_misses=0;lexicon_version=2|-";
    var events = PipelineRuntimeFixture.readableEvents("framework noise\n" + line);
    assertEquals(1, events.size());
    assertEquals(id.toString(), events.getFirst().path("request_id").stringValue());
    assertEquals(0, events.getFirst().path("counts").path("cache_misses").asInt(-1));
    assertEquals(2, events.getFirst().path("lexicon_version").asInt());
    var rollback =
        PipelineRuntimeFixture.readableEvents(
            "10-04 01:02:03|ERROR|"
                + id
                + "|lexicon.import.completed|reason=PUBLISH_ROLLED_BACK|-");
    assertEquals("PUBLISH_ROLLED_BACK", rollback.getFirst().path("reason").stringValue());
    assertTrue(
        PipelineRuntimeFixture.readableEvents("{\"schema\":\"lexiflow.event.v1\"}").isEmpty());
  }

  @Test
  void rejectsMalformedFieldsInsteadOfInventingZeroCounts() {
    var prefix = "10-04 01:02:03|INFO|" + UUID.randomUUID() + "|caption.request.completed|";
    assertThrows(
        AssertionError.class,
        () -> PipelineRuntimeFixture.readableEvents(prefix + "count_cache_misses=invalid|-"));
    assertThrows(
        AssertionError.class,
        () -> PipelineRuntimeFixture.readableEvents(prefix + "missing-separator|-"));
  }
}
