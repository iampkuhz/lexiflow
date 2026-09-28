package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.api.hints.model.CaptionHintResponse;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import io.lexiflow.observability.platform.FileSegmentAnalysisStore;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.slf4j.LoggerFactory;
import org.springframework.mock.web.MockHttpServletRequest;

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
      controller.hint(request, observation());
      controller.hint(request, observation());
      controller(file).hint(request, observation());
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
    controller(file)
        .hint(
            request(segment("old", "reliable", false), segment("new", "zxqv", true)),
            observation());
    var lines = Files.readAllLines(file);
    assertEquals(1, lines.size());
    assertTrue(lines.getFirst().contains("\"status\":\"NO_HINT\""));
    assertTrue(lines.getFirst().contains("\"translatedRanges\":[]"));
    assertTrue(lines.getFirst().contains("\"untranslatedRanges\":[{\"start\":0,\"end\":4}]"));
  }

  @Test
  void crossSegmentHintHasOneAnchorButBothSegmentsAreCovered() throws IOException {
    var file = directory.resolve("cross-segment.jsonl");
    controller(file)
        .hint(request(segment("a", "reli", true), segment("b", "able", true)), observation());
    var lines = Files.readAllLines(file);
    assertEquals(2, lines.size());
    assertTrue(lines.get(0).contains("\"english\":\"reli\""));
    assertTrue(lines.get(0).contains("\"anchor\":true"));
    assertTrue(lines.get(1).contains("\"english\":\"able\""));
    assertTrue(lines.get(1).contains("\"anchor\":false"));
    assertTrue(lines.stream().allMatch(line -> line.contains("\"untranslatedRanges\":[]")));
  }

  @Test
  void lateCompletionKeepsOldSegmentIdAndRecordsOnlyTheNewSuffix() throws IOException {
    var file = directory.resolve("late-completion.jsonl");
    var controller = controller(file);
    controller.hint(request(segment("stable-old", "reli", true)), observation());
    var firstLine = Files.readAllLines(file).getFirst();
    var result =
        (CaptionHintResponse)
            controller
                .hint(
                    request(segment("stable-old", "reli", false), segment("fresh", "able", true)),
                    observation())
                .getBody();
    assertEquals(List.of("fresh"), result.processedKeys());
    assertEquals("stable-old", result.hints().getFirst().startKey());
    assertEquals("fresh", result.hints().getFirst().endKey());
    var lines = Files.readAllLines(file);
    assertEquals(2, lines.size());
    assertEquals(firstLine, lines.getFirst());
    assertTrue(lines.getFirst().contains("\"status\":\"NO_HINT\""));
    assertTrue(lines.get(1).contains("\"status\":\"HINTED\""));
    assertTrue(lines.get(1).contains("\"anchor\":false"));
  }

  @Test
  void unavailableFileDoesNotBlockHintsOrLeakCaptionToOrdinaryLog() throws IOException {
    var path = directory.resolve("unavailable.jsonl");
    Files.createDirectory(path);
    var eventLines = new java.util.ArrayList<String>();
    var response =
        (CaptionHintResponse)
            controller(path, null, new StructuredEventLogger((level, json) -> eventLines.add(json)))
                .hint(request(segment("new", "reliable", true)), observation())
                .getBody();
    assertEquals(List.of("new"), response.processedKeys());
    assertEquals(1, response.hints().size());
    assertEquals(1, eventLines.size());
    assertTrue(eventLines.getFirst().contains("analysis.record.failed"));
    assertTrue(eventLines.getFirst().contains("attempted_segments"));
    assertFalse(eventLines.getFirst().contains("reliable"));
  }

  @Test
  void dedicatedConsoleMirrorsOnlyNewPersistedRecordsAndEscapesControlCharacters()
      throws IOException {
    var path = directory.resolve("console.jsonl");
    var output = new ByteArrayOutputStream();
    var console = new PrintStream(output, true, StandardCharsets.UTF_8);
    var request = request(segment("console-new", "reliable\n\u001b[31m\rspoof", true));
    var controller = controller(path, console);
    controller.hint(request, observation());
    controller.hint(request, observation());
    controller(path, console).hint(request, observation());
    var lines = output.toString(StandardCharsets.UTF_8).lines().toList();
    assertEquals(1, lines.size());
    assertEquals("[LexiFlow segment] " + Files.readAllLines(path).getFirst(), lines.getFirst());
    assertTrue(lines.getFirst().contains("可靠的"));
    assertTrue(lines.getFirst().contains("\\n"));
    assertFalse(lines.getFirst().contains("\u001b"));
    assertFalse(lines.getFirst().contains("secret-topic"));
    assertFalse(lines.getFirst().contains("console-new"));
  }

  @Test
  void dedicatedConsoleShowsNoHintButNeverClaimsFailedPersistence() throws IOException {
    var output = new ByteArrayOutputStream();
    var console = new PrintStream(output, true, StandardCharsets.UTF_8);
    controller(directory.resolve("empty.jsonl"), console)
        .hint(request(segment("empty", "zxqv", true)), observation());
    assertTrue(output.toString(StandardCharsets.UTF_8).contains("NO_HINT"));
    output.reset();
    var blocked = directory.resolve("blocked");
    Files.createDirectory(blocked);
    assertEquals(
        1,
        ((CaptionHintResponse)
                controller(blocked, console)
                    .hint(request(segment("bad", "reliable", true)), observation())
                    .getBody())
            .hints()
            .size());
    assertEquals("", output.toString(StandardCharsets.UTF_8));
  }

  private static CaptionHintController controller(Path path) throws IOException {
    return controller(path, null);
  }

  private static CaptionHintController controller(Path path, PrintStream console)
      throws IOException {
    return controller(path, console, new StructuredEventLogger());
  }

  private static CaptionHintController controller(
      Path path, PrintStream console, StructuredEventLogger events) throws IOException {
    return new CaptionHintController(
        new EnrichCaptionUseCase(new BuiltinLexiconCatalog(), new DeterministicHintPolicy()),
        new SegmentAnalysisLog(
            new FileSegmentAnalysisStore(
                path, console == null ? null : line -> console.print(line))),
        events);
  }

  private static MockHttpServletRequest observation() {
    var request = new MockHttpServletRequest();
    request.setAttribute(
        CaptionRequestObservation.ATTRIBUTE,
        new CaptionRequestObservation(UUID.randomUUID(), System.nanoTime()));
    return request;
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
