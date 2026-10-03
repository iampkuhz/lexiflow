package io.lexiflow.lexicon.application.importing;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import java.util.ArrayList;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

/** 验证单次导入观察的步骤幂等、心跳限速及故障隔离。 */
final class LexiconImportObservationTest {
  @Test
  void emitsOneTerminalAndRateLimitsActualHeartbeats() {
    var time = new AtomicLong(10);
    var events = new ArrayList<LexiconImportObserver.Event>();
    var observation = new LexiconImportObservation(events::add, time::get);
    observation.start(LexiconImportObserver.Step.PREPARE);
    observation.heartbeat(LexiconImportObserver.Step.PREPARE);
    time.addAndGet(180_000_000_000L);
    observation.heartbeat(LexiconImportObserver.Step.PREPARE);
    observation.terminal(LexiconImportObserver.Reason.OK, Map.of(), Map.of(), 0L);
    observation.terminal(LexiconImportObserver.Reason.INTERNAL_ERROR, Map.of(), Map.of(), null);
    observation.start(LexiconImportObserver.Step.PUBLISH);
    assertEquals(4, events.size());
    assertEquals(1, events.stream().filter(LexiconImportObserver.Event::terminal).count());
    assertEquals(LexiconImportObserver.Reason.OK, events.get(1).reason());
  }

  @Test
  void observerFailureNeverMasksCallerFailure() {
    var observation =
        new LexiconImportObservation(
            event -> {
              throw new IllegalStateException("synthetic");
            });
    observation.start(LexiconImportObserver.Step.PUBLISH);
    observation.complete(
        LexiconImportObserver.Step.PUBLISH,
        LexiconImportObserver.Reason.INTERNAL_ERROR,
        Map.of(),
        Map.of());
    var expected = new IllegalArgumentException("business failure");
    assertEquals(
        expected,
        assertThrows(
            IllegalArgumentException.class,
            () -> {
              throw expected;
            }));
  }

  @Test
  void terminalWithoutStartedStepUsesRunConstructionAsDurationOrigin() {
    var time = new AtomicLong(9_000_000_000L);
    var events = new ArrayList<LexiconImportObserver.Event>();
    var observation = new LexiconImportObservation(events::add, time::get);
    time.addAndGet(17);
    observation.terminal(LexiconImportObserver.Reason.SOURCE_INVALID, Map.of(), Map.of(), null);
    observation.start(LexiconImportObserver.Step.SOURCE_CHECK);
    observation.heartbeat(LexiconImportObserver.Step.SOURCE_CHECK);

    assertEquals(1, events.size());
    assertEquals(17, events.getFirst().durationNanos());
    assertEquals(null, events.getFirst().lexiconVersion());
  }
}
