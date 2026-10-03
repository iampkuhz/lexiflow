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
            "caption.incremental",
            Map.of("video", "abcdefghijk", "subtitle", "line-7", "position_ms", "42"),
            "English(短释)\nnext");
    assertEquals(
        "01-01 00:00:00|INFO|123e4567-e89b-12d3-a456-426614174000|caption.incremental|position_ms=42;subtitle=line-7;video=abcdefghijk|English(短释)\\nnext",
        line);
    assertEquals(6, line.split("\\|", -1).length);
    assertFalse(line.contains("\n"));
  }

  @Test
  void emitsSixUnpaddedColumnsWithEscapedBodyAndLocation() {
    var id = UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
    var formatter = new ReadableLogFormatter(Clock.fixed(Instant.EPOCH, ZoneOffset.UTC));
    var line =
        formatter.format(
            "INFO",
            id,
            "caption.incremental",
            Map.of("topic", "a|b;c=d\n\\"),
            "显示|内容\r\n\t\u0001\\");
    assertEquals(
        "01-01 00:00:00|INFO|123e4567-e89b-12d3-a456-426614174000|caption.incremental|topic=a\\u007Cb\\u003Bc\\u003Dd\\n\\\\|显示\\u007C内容\\r\\n\\t\\u0001\\\\",
        line);
    assertEquals(6, line.split("\\|", -1).length);
    String location = line.substring(line.indexOf("topic="), line.lastIndexOf('|'));
    assertEquals(1, location.split(";", -1).length);
    assertThrows(
        IllegalArgumentException.class,
        () -> formatter.format("INFO", id, "bad\nevent", Map.of(), "text"));
  }
}
