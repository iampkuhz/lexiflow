package io.lexiflow.api.captiondebug;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.observability.platform.StructuredEventLogger;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CaptionDebugControllerTest {
  @Test
  void capabilityRequiresBothExplicitSwitchesAndAllowsHostBridgeAddresses() {
    var enabled = new CaptionDebugController(true, true, new StructuredEventLogger());
    var allowed = enabled.capability();
    assertTrue(allowed.getBody().enabled());
    assertEquals("no-store", allowed.getHeaders().getFirst("Cache-Control"));
    assertFalse(
        new CaptionDebugController(true, false, new StructuredEventLogger())
            .capability()
            .getBody()
            .enabled());
    assertFalse(
        new CaptionDebugController(false, true, new StructuredEventLogger())
            .capability()
            .getBody()
            .enabled());
  }

  @Test
  void acceptsOnlyBoundedEnumeratedEventsAndNeverCachesResponses() {
    var controller = new CaptionDebugController(true, true, new StructuredEventLogger());
    var request =
        new CaptionDebugRequest(
            UUID.randomUUID().toString(),
            "video-start",
            "topic-key",
            "abcdefghijk",
            "subtitle-key",
            42L,
            "实际显示的字幕|中文");
    var accepted = controller.append(request);
    assertEquals(204, accepted.getStatusCode().value());
    assertEquals("no-store", accepted.getHeaders().getFirst("Cache-Control"));

    assertEquals(
        400,
        controller
            .append(
                new CaptionDebugRequest(
                    request.eventId(), "INFO|forged", "topic", null, null, null, "x"))
            .getStatusCode()
            .value());
    assertEquals(
        400,
        controller
            .append(
                new CaptionDebugRequest(
                    request.eventId(), "final", "topic\nforged", null, null, null, "x"))
            .getStatusCode()
            .value());
    assertEquals(
        404,
        new CaptionDebugController(false, true, new StructuredEventLogger())
            .append(request)
            .getStatusCode()
            .value());
  }
}
