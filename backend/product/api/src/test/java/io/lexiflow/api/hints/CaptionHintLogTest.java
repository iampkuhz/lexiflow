package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;

/** 敏感字幕只在专用本机文件按片段记一次，不进入普通 API 日志。 */
class CaptionHintLogTest {
  @TempDir Path directory;

  @Test
  void recordsCurrentSegmentOnceAcrossRetriesAndApiRestart() throws IOException {
    var file = directory.resolve("caption-segments.jsonl");
    var request =
        request(
            segment("old-key", "old text ", false),
            segment("new-key", "reliable\nfake=\"x\"", true));
    var controller = controller(file);
    var logger = (Logger) LoggerFactory.getLogger(CaptionHintController.class);
    var appender = new ListAppender<ILoggingEvent>();
    appender.start();
    logger.addAppender(appender);
    try {
      controller.hint(request);
      controller.hint(request);
      controller(file).hint(request);
      assertTrue(
          appender.list.stream()
              .noneMatch(event -> event.getFormattedMessage().contains("reliable")));
    } finally {
      logger.detachAppender(appender);
      appender.stop();
    }
    var lines = Files.readAllLines(file);
    assertEquals(1, lines.size());
    assertTrue(lines.getFirst().contains("\"english\":\"reliable\\nfake=\\\"x\\\"\""));
    assertTrue(lines.getFirst().contains("\"status\":\"HINTED\""));
    assertTrue(lines.getFirst().contains("\"gloss\":\"可靠的\""));
    assertFalse(lines.getFirst().contains("old text"));
    assertFalse(lines.getFirst().contains("old-key"));
    assertFalse(lines.getFirst().contains("new-key"));
    assertFalse(lines.getFirst().contains("secret-topic"));
    assertFalse(lines.getFirst().contains("\nfake"));
  }

  @Test
  void recordsNoHintWithCompleteUntranslatedRange() throws IOException {
    var file = directory.resolve("no-hint.jsonl");
    controller(file).hint(request(segment("old", "reliable", false), segment("new", "zxqv", true)));
    var lines = Files.readAllLines(file);
    assertEquals(1, lines.size());
    assertTrue(lines.getFirst().contains("\"status\":\"NO_HINT\""));
    assertTrue(lines.getFirst().contains("\"translatedRanges\":[]"));
    assertTrue(lines.getFirst().contains("\"untranslatedRanges\":[{\"start\":0,\"end\":4}]"));
  }

  @Test
  void crossSegmentHintHasOneAnchorButBothSegmentsAreCovered() throws IOException {
    var file = directory.resolve("cross-segment.jsonl");
    controller(file).hint(request(segment("a", "reli", true), segment("b", "able", true)));
    var lines = Files.readAllLines(file);
    assertEquals(2, lines.size());
    assertTrue(lines.get(0).contains("\"english\":\"reli\""));
    assertTrue(lines.get(0).contains("\"anchor\":true"));
    assertTrue(lines.get(1).contains("\"english\":\"able\""));
    assertTrue(lines.get(1).contains("\"anchor\":false"));
    assertTrue(lines.stream().allMatch(line -> line.contains("\"untranslatedRanges\":[]")));
  }

  @Test
  void unavailableFileDoesNotBlockHintsOrLeakCaptionToOrdinaryLog() throws IOException {
    var path = directory.resolve("unavailable.jsonl");
    Files.createDirectory(path);
    var logger = (Logger) LoggerFactory.getLogger(CaptionHintController.class);
    var appender = new ListAppender<ILoggingEvent>();
    appender.start();
    logger.addAppender(appender);
    try {
      var response = controller(path).hint(request(segment("new", "reliable", true))).getBody();
      assertEquals(List.of("new"), response.processedKeys());
      assertEquals(1, response.hints().size());
      assertEquals(1, appender.list.size());
      assertFalse(appender.list.getFirst().getFormattedMessage().contains("reliable"));
    } finally {
      logger.detachAppender(appender);
      appender.stop();
    }
  }

  private static CaptionHintController controller(Path path) throws IOException {
    return new CaptionHintController(
        new EnrichCaptionUseCase(new BuiltinLexiconCatalog(), new DeterministicHintPolicy()),
        new SegmentAnalysisLog(path));
  }

  private static CaptionHintRequest request(CaptionHintRequest.Segment... segments) {
    return new CaptionHintRequest(
        "secret-topic",
        null,
        null,
        new CaptionHintRequest.Snapshot(
            List.of(new CaptionHintRequest.CaptionGroup(null, null, List.of(segments)))));
  }

  private static CaptionHintRequest.Segment segment(String key, String text, boolean append) {
    return new CaptionHintRequest.Segment(key, text, null, append, 0L);
  }
}
