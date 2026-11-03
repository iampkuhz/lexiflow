package io.lexiflow.api.captiondebug;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.observability.platform.StructuredEventLogger;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CaptionDebugControllerTest {
  private static final String EXTENSION_ORIGIN =
      "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

  @Test
  void consoleIsCompactWhileOriginalEventIdentityStillDeduplicates() {
    var lines = new java.util.ArrayList<String>();
    var controller =
        new CaptionDebugController(
            true, new StructuredEventLogger(null, Runnable::run, lines::add));
    String id = UUID.randomUUID().toString();
    var request =
        new CaptionDebugRequest(
            id,
            "incremental",
            "private-topic",
            "abcdefghijk",
            "private-subtitle",
            543129L,
            "synthetic|caption\n中文");
    assertEquals(204, controller.append(EXTENSION_ORIGIN, request).getStatusCode().value());
    assertEquals(204, controller.append(EXTENSION_ORIGIN, request).getStatusCode().value());
    assertEquals(1, lines.size());
    var columns = lines.getFirst().split("\\|", -1);
    assertEquals(6, columns.length);
    assertEquals("字幕增量", columns[2]);
    assertEquals("video=abcdefghijk", columns[3]);
    assertEquals("09:03.129", columns[4]);
    assertEquals("synthetic\\u007Ccaption\\n中文", columns[5]);
    assertFalse(lines.getFirst().contains(id));
    assertFalse(lines.getFirst().contains("private-"));
  }

  @Test
  void capabilityDefaultsOnAndCanBeDisabledWithoutAnalysisFileSwitch() {
    var enabled = new CaptionDebugController(true, new StructuredEventLogger());
    var allowed = enabled.capability(EXTENSION_ORIGIN);
    assertTrue(allowed.getBody().enabled());
    assertEquals("no-store", allowed.getHeaders().getFirst("Cache-Control"));
    assertFalse(
        new CaptionDebugController(false, new StructuredEventLogger())
            .capability(EXTENSION_ORIGIN)
            .getBody()
            .enabled());
  }

  @Test
  void acceptsOnlyBoundedEnumeratedEventsAndNeverCachesResponses() {
    // 本测试只验证请求合同；同步自有 sink 防止延迟日志污染后续 CLI 的严格 stdout 检查。
    var lines = new java.util.ArrayList<String>();
    var logger = new StructuredEventLogger(null, Runnable::run, lines::add);
    var controller = new CaptionDebugController(true, logger);
    var request =
        new CaptionDebugRequest(
            UUID.randomUUID().toString(),
            "video-start",
            "topic-key",
            "abcdefghijk",
            "subtitle-key",
            42L,
            "实际显示的字幕|中文");
    var accepted = controller.append(null, request);
    assertEquals(204, accepted.getStatusCode().value());
    assertEquals("no-store", accepted.getHeaders().getFirst("Cache-Control"));
    assertEquals(204, controller.append(null, request).getStatusCode().value());

    assertEquals(
        400,
        controller
            .append(
                EXTENSION_ORIGIN,
                new CaptionDebugRequest(
                    request.eventId(), "INFO|forged", "topic", null, null, null, "x"))
            .getStatusCode()
            .value());
    assertEquals(
        400,
        controller
            .append(
                EXTENSION_ORIGIN,
                new CaptionDebugRequest(
                    UUID.randomUUID().toString(),
                    "final",
                    "topic",
                    "abcdefghijk",
                    "subtitle",
                    null,
                    "x"))
            .getStatusCode()
            .value());
    assertEquals(
        400,
        controller
            .append(
                EXTENSION_ORIGIN,
                new CaptionDebugRequest(
                    request.eventId(), "final", "topic\nforged", null, null, null, "x"))
            .getStatusCode()
            .value());
    assertEquals(
        404,
        new CaptionDebugController(false, logger)
            .append(EXTENSION_ORIGIN, request)
            .getStatusCode()
            .value());
    assertEquals(403, controller.append("https://evil.invalid", request).getStatusCode().value());
    assertTrue(controller.capability(null).getBody().enabled());
    assertEquals(1, lines.size(), "重复、禁用或被拒绝的请求不得追加日志");
    assertTrue(lines.getFirst().contains("实际显示的字幕\\u007C中文"));
  }
}
