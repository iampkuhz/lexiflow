package io.lexiflow.observability.platform;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ReadableLogFormatterTest {
  @Test
  void formatHasRequiredSixColumnsAndSinglePhysicalLine() {
    var id = UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
    var formatter = new ReadableLogFormatter(Clock.fixed(Instant.EPOCH, ZoneOffset.UTC));
    var line =
        formatter.format(
            "INFO",
            id,
            "incremental",
            Map.of("video", "abcdefghijk", "subtitle", "line-7", "position_ms", "42"),
            "English(短释)\nnext");
    assertEquals("01-01 00:00:00|INFO|字幕增量|video=abcdefghijk|00:00.042|English(短释)\\nnext", line);
    assertEquals(6, line.split("\\|", -1).length);
    assertFalse(line.contains("\n"));
  }

  @Test
  void diagnosticsRetainFullIdentityAndEscapeBodyAndLocation() {
    var id = UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
    var formatter = new ReadableLogFormatter(Clock.fixed(Instant.EPOCH, ZoneOffset.UTC));
    var line =
        formatter.format(
            "WARN",
            id,
            "caption.incremental",
            Map.of("topic", "a|b;c=d\n\\"),
            "显示|内容\r\n\t\u0001\\");
    assertEquals(
        "01-01 00:00:00|WARN|123e4567-e89b-12d3-a456-426614174000|caption.incremental|topic=a\\u007Cb\\u003Bc\\u003Dd\\n\\\\|显示\\u007C内容\\r\\n\\t\\u0001\\\\",
        line);
    assertEquals(6, line.split("\\|", -1).length);
    String location = line.substring(line.indexOf("topic="), line.lastIndexOf('|'));
    assertEquals(1, location.split(";", -1).length);
    assertThrows(
        IllegalArgumentException.class,
        () -> formatter.format("WARN", id, "bad\nevent", Map.of(), "text"));
  }

  @Test
  void captionTimeDoesNotWrapAndInternalKeysAreNotDisplayed() {
    var id = UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
    var formatter = new ReadableLogFormatter(Clock.fixed(Instant.EPOCH, ZoneOffset.UTC));
    for (var entry :
        Map.of("video-start", "视频开始", "incremental", "字幕增量", "final", "字幕收尾", "interrupted", "字幕中断")
            .entrySet()) {
      var line =
          formatter.format(
              "INFO",
              id,
              entry.getKey(),
              Map.of(
                  "video",
                  "abcdefghijk",
                  "position_ms",
                  "3600001",
                  "subtitle",
                  "private-subtitle",
                  "topic",
                  "private-topic",
                  "segment",
                  "private-segment"),
              "synthetic|text\n中文");
      assertEquals(
          "01-01 00:00:00|INFO|"
              + entry.getValue()
              + "|video=abcdefghijk|60:00.001|synthetic\\u007Ctext\\n中文",
          line);
      assertFalse(line.contains(id.toString()));
      assertFalse(line.contains("private-"));
    }
    for (String position : new String[] {"0", "59999", "60000", "543129"}) {
      var expected =
          Map.of(
                  "0",
                  "00:00.000",
                  "59999",
                  "00:59.999",
                  "60000",
                  "01:00.000",
                  "543129",
                  "09:03.129")
              .get(position);
      assertEquals(
          "01-01 00:00:00|INFO|字幕增量|video=abcdefghijk|" + expected + "|x",
          formatter.format(
              "INFO",
              id,
              "incremental",
              Map.of("video", "abcdefghijk", "position_ms", position),
              "x"));
    }
    assertEquals(
        "01-01 00:00:00|INFO|字幕增量|video=-|-|x",
        formatter.format("INFO", id, "incremental", Map.of(), "x"));
  }

  @Test
  void ordinaryInfoDoesNotInventOrDisplayCorrelationIds() {
    var formatter = new ReadableLogFormatter(Clock.fixed(Instant.EPOCH, ZoneOffset.UTC));
    assertEquals(
        "01-01 00:00:00|INFO|runtime.start.completed|-|-",
        formatter.format("INFO", UUID.randomUUID(), "runtime.start.completed", Map.of(), "-"));
    assertEquals(
        "01-01 00:00:00|ERROR|-|runtime.start.completed|-|-",
        formatter.format("ERROR", null, "runtime.start.completed", Map.of(), "-"));
  }
}
