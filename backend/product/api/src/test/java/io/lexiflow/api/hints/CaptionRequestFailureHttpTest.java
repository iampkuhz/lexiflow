package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import io.lexiflow.api.runtime.LexiconRuntime;
import io.lexiflow.lexicon.application.port.InvalidPublishedLexiconException;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.observability.platform.SegmentAnalysisStore;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;
import org.springframework.dao.DataAccessResourceFailureException;

/** 使用隔离合成 Repository 检查未发布、依赖、内部、混版和台账失败的 HTTP 终态。 */
@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "lexiflow.runtime.mode=formal",
      "lexiflow.segment-analysis.enabled=true",
      "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-failure-${random.uuid}.jsonl"
    })
@Import(CaptionRequestFailureHttpTest.TestBeans.class)
@org.springframework.test.context.ActiveProfiles("caption-request-failure-test")
class CaptionRequestFailureHttpTest {
  @LocalServerPort private int port;
  @Autowired private LexiconRuntime runtime;

  @BeforeEach
  void resetSyntheticRepository() {
    SyntheticRepository.version = 7;
    SyntheticRepository.failure = Failure.NONE;
    SyntheticRepository.mixed = false;
    SyntheticRepository.lookups.set(0);
    runtime.probe();
  }

  @Test
  void runtimeStatusUsesCurrentSyntheticPublicationAndFixedFailureReason() throws Exception {
    try (var client = HttpClient.newHttpClient()) {
      var first = status(client);
      var mapper = new tools.jackson.databind.ObjectMapper();
      var ready = mapper.readTree(first.body());
      assertEquals(200, first.statusCode());
      assertEquals(7, ready.get("datasetVersion").asLong());
      assertTrue(ready.get("ready").asBoolean());

      SyntheticRepository.version = 0;
      var empty = mapper.readTree(status(client).body());
      assertFalse(empty.get("ready").asBoolean());
      assertEquals("NO_PUBLISHED_DATA", empty.get("reason").asString());
      assertEquals(0, empty.get("datasetVersion").asLong());

      SyntheticRepository.failure = Failure.DEPENDENCY;
      var unavailable = mapper.readTree(status(client).body());
      assertFalse(unavailable.get("ready").asBoolean());
      assertEquals("DEPENDENCY_UNAVAILABLE", unavailable.get("reason").asString());
      assertEquals("null", unavailable.get("datasetVersion").toString());

      SyntheticRepository.failure = Failure.SCHEMA;
      var schema = mapper.readTree(status(client).body());
      assertFalse(schema.get("ready").asBoolean());
      assertEquals("SCHEMA_MISMATCH", schema.get("reason").asString());

      SyntheticRepository.failure = Failure.PREWARM;
      SyntheticRepository.version = 8;
      runtime.probe();
      var degraded = mapper.readTree(status(client).body());
      assertTrue(degraded.get("ready").asBoolean());
      assertEquals("PREWARM_DEGRADED", degraded.get("reason").asString());
      assertEquals(8, degraded.get("datasetVersion").asLong());

      SyntheticRepository.failure = Failure.NONE;
      SyntheticRepository.version = 9;
      runtime.probe();
      var changed = mapper.readTree(status(client).body());
      assertTrue(changed.get("ready").asBoolean());
      assertEquals("OK", changed.get("reason").asString());
      assertEquals(9, changed.get("datasetVersion").asLong());
    }
  }

  private HttpResponse<String> status(HttpClient client) throws Exception {
    return client.send(
        HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/api/v1/runtime-status"))
            .GET()
            .build(),
        HttpResponse.BodyHandlers.ofString());
  }

  @Test
  void noPublishedDataIsA503WithFrozenReason() throws Exception {
    SyntheticRepository.version = 0;
    runtime.probe();
    var captured = requestWithEvents(payload(segment("new", "caption-private", true)));
    assertEquals(503, captured.response.statusCode());
    assertTrue(captured.response.body().contains("LEXICON_NOT_READY"));
    assertEquals("NO_PUBLISHED_DATA", captured.terminal);
    assertFalse(captured.logText.contains("caption-private"));
  }

  @Test
  void dependencyFailureUses503AndInternalFailureUsesFixed500WithoutPayload() throws Exception {
    SyntheticRepository.failure = Failure.DEPENDENCY;
    var dependency = requestWithEvents(payload(segment("new", "private-dependency", true)));
    assertEquals(503, dependency.response.statusCode());
    assertEquals("DEPENDENCY_UNAVAILABLE", dependency.terminal);

    resetSyntheticRepository();
    SyntheticRepository.failure = Failure.INTERNAL;
    var internal = requestWithEvents(payload(segment("new", "private-internal", true)));
    assertEquals(500, internal.response.statusCode());
    assertTrue(internal.response.body().contains("INTERNAL_ERROR"));
    assertEquals("INTERNAL_ERROR", internal.terminal);
    assertFalse(internal.response.body().contains("private-internal"));
    assertFalse(internal.logText.contains("private-internal"));
    assertFalse(internal.logText.contains("private-repository-exception"));
  }

  @Test
  void getMethodFallbackEmitsInvalidRequestReason() throws Exception {
    var logger = (Logger) LoggerFactory.getLogger(StructuredEventLogger.class);
    var originalLevel = logger.getLevel();
    logger.setLevel(ch.qos.logback.classic.Level.DEBUG);
    var appender = new ListAppender<ILoggingEvent>();
    appender.start();
    logger.addAppender(appender);
    try (var client = HttpClient.newHttpClient()) {
      var response =
          client.send(
              HttpRequest.newBuilder(
                      URI.create("http://127.0.0.1:" + port + "/api/v1/caption-hints"))
                  .GET()
                  .build(),
              HttpResponse.BodyHandlers.ofString());
      assertEquals(405, response.statusCode());
      var id = response.headers().firstValue("X-Request-ID").orElseThrow();
      var event =
          appender.list.stream()
              .map(ILoggingEvent::getFormattedMessage)
              .filter(line -> line.contains(id))
              .findFirst()
              .orElseThrow();
      assertTrue(event.contains("reason=INVALID_REQUEST"));
    } finally {
      logger.setLevel(originalLevel);
      logger.detachAppender(appender);
      appender.stop();
    }
  }

  @Test
  void mixedPublishedVersionsReturn503AndDoNotAttemptSensitiveRecord() throws Exception {
    SyntheticRepository.mixed = true;
    var captured =
        requestWithEvents(
            payload(segment("a", "reliable", true) + "," + segment("b", "example", true, 1)));
    assertEquals(503, captured.response.statusCode());
    assertTrue(captured.response.body().contains("VERSION_CONFLICT"));
    assertEquals("VERSION_CONFLICT", captured.terminal);
    assertFalse(captured.logText.contains("analysis.record.failed"));
  }

  @Test
  void sensitiveRecordFailureKeepsSuccessfulResponseAndSharesRequestId() throws Exception {
    var captured = requestWithEvents(payload(segment("new", "private-caption", true)));
    assertEquals(200, captured.response.statusCode());
    assertEquals("NO_HINT", captured.terminal);
    var id = captured.response.headers().firstValue("X-Request-ID").orElseThrow();
    assertTrue(captured.logText.contains("analysis.record.failed"));
    var analysisEvents =
        captured.logText.lines().filter(line -> line.contains("analysis.record.failed")).toList();
    assertEquals(1, analysisEvents.size());
    assertTrue(analysisEvents.getFirst().contains(id));
    assertFalse(captured.logText.contains("private-analysis-failure"));
    assertTrue(captured.logText.contains("attempted_segments"));
    assertFalse(captured.logText.contains("private-caption"));
  }

  private Captured requestWithEvents(String body) throws Exception {
    var logger = (Logger) LoggerFactory.getLogger(StructuredEventLogger.class);
    var originalLevel = logger.getLevel();
    logger.setLevel(ch.qos.logback.classic.Level.DEBUG);
    var appender = new ListAppender<ILoggingEvent>();
    appender.start();
    logger.addAppender(appender);
    try {
      var response = post(body);
      var lines = appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
      var terminal =
          lines.stream()
              .filter(line -> line.contains("caption.request.completed"))
              .findFirst()
              .orElseThrow();
      assertEquals(
          1, lines.stream().filter(line -> line.contains("caption.request.completed")).count());
      return new Captured(response, reason(terminal), String.join("\n", lines));
    } finally {
      logger.setLevel(originalLevel);
      logger.detachAppender(appender);
      appender.stop();
    }
  }

  private static String reason(String event) {
    var columns = event.split("\\|", -1);
    assertEquals(6, columns.length);
    return java.util.Arrays.stream(columns[4].split(";"))
        .filter(field -> field.startsWith("reason="))
        .map(field -> field.substring("reason=".length()))
        .findFirst()
        .orElse("OK");
  }

  private HttpResponse<String> post(String body) throws Exception {
    try (var client = HttpClient.newHttpClient()) {
      return client.send(
          HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/api/v1/caption-hints"))
              .header("Content-Type", "application/json")
              .POST(HttpRequest.BodyPublishers.ofString(body))
              .build(),
          HttpResponse.BodyHandlers.ofString());
    }
  }

  private static String payload(String segments) {
    return "{\"captionTopicKey\":\"topic\",\"trackKey\":null,\"lastRequestedSnapshot\":null,\"currentSnapshot\":{\"captions\":[{\"windowId\":null,\"startMs\":null,\"segments\":["
        + segments
        + "]}]}}";
  }

  private static String segment(String key, String text, boolean append) {
    return segment(key, text, append, 0);
  }

  private static String segment(String key, String text, boolean append, int line) {
    return "{\"key\":\""
        + key
        + "\",\"text\":\""
        + text
        + "\",\"offsetMs\":null,\"append\":"
        + append
        + ",\"line\":"
        + line
        + "}";
  }

  private record Captured(HttpResponse<String> response, String terminal, String logText) {}

  private enum Failure {
    NONE,
    DEPENDENCY,
    INTERNAL,
    SCHEMA,
    PREWARM
  }

  private static final class SyntheticRepository implements LexiconReadRepository {
    private static volatile long version = 7;
    private static volatile Failure failure = Failure.NONE;
    private static volatile boolean mixed;
    private static final AtomicInteger lookups = new AtomicInteger();

    @Override
    public long publishedVersion() {
      if (failure == Failure.SCHEMA) throw new InvalidPublishedLexiconException();
      if (failure == Failure.DEPENDENCY)
        throw new DataAccessResourceFailureException("private-repository-exception");
      return mixed && lookups.get() > 0 ? 8 : version;
    }

    @Override
    public List<LexiconHintCandidate> findByForms(long requestedVersion, Collection<String> forms) {
      if (failure == Failure.INTERNAL)
        throw new NullPointerException("private-repository-exception");
      lookups.incrementAndGet();
      var result = new java.util.ArrayList<LexiconHintCandidate>();
      if (mixed && forms.contains("reliable"))
        result.add(candidate("reliable-entry", "reliable", 7));
      if (mixed && forms.contains("example")) result.add(candidate("example-entry", "example", 8));
      return List.copyOf(result);
    }

    @Override
    public List<LexiconHintCandidate> findPrewarmForms(
        long requestedVersion, LexiconHintAction action, int limit) {
      if (failure == Failure.PREWARM && action == LexiconHintAction.HINT)
        throw new DataAccessResourceFailureException("private-prewarm-exception");
      return List.of();
    }

    private static LexiconHintCandidate candidate(String entry, String form, long version) {
      return new LexiconHintCandidate(
          io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId(entry),
          UUID.nameUUIDFromBytes(
              (entry + "-sense").getBytes(java.nio.charset.StandardCharsets.UTF_8)),
          version,
          "en",
          form,
          form,
          LexiconEntryKind.WORD,
          LexiconHintAction.HINT,
          "合成释义",
          500,
          false,
          0);
    }
  }

  @TestConfiguration(proxyBeanMethods = false)
  @org.springframework.context.annotation.Profile("caption-request-failure-test")
  static class TestBeans {
    @Bean
    @Primary
    LexiconReadRepository syntheticRepository() {
      return new SyntheticRepository();
    }

    @Bean
    @Primary
    SegmentAnalysisLog failingAnalysisLog() {
      SegmentAnalysisStore store =
          records -> {
            throw new IOException("private-analysis-failure");
          };
      return new SegmentAnalysisLog(store);
    }
  }
}
