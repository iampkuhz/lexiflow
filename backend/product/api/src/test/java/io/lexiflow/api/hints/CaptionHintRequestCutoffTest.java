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
    var request = new CaptionHintRequest(List.of(), snapshot);
    assertEquals(1500, request.toDomain().commonWordCutoff());
    for (int cutoff : List.of(1000, 1500, 2000)) {
      assertEquals(cutoff, request.toDomain(cutoff).commonWordCutoff());
    }
    assertThrows(
        IllegalArgumentException.class, () -> request.toDomain(1001));
  }
}
