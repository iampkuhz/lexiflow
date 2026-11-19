package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.api.hints.model.CaptionHintRequest;
import java.util.List;
import org.junit.jupiter.api.Test;

class CaptionHintRequestCutoffTest {
  @Test
  void defaultsAndAcceptsOnlyPublishedCutoffChoices() {
    var snapshot = new CaptionHintRequest.Snapshot(List.of());
    assertEquals(
        1500, new CaptionHintRequest("topic", null, null, snapshot).toDomain().commonWordCutoff());
    for (int cutoff : List.of(1000, 1500, 2000)) {
      assertEquals(
          cutoff,
          new CaptionHintRequest("topic", null, null, snapshot, cutoff)
              .toDomain()
              .commonWordCutoff());
    }
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionHintRequest("topic", null, null, snapshot, 1001));
  }
}
