package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

/** 使用真实 HTTP 验证请求身份、终态次数和普通事件隐私边界。 */
@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "lexiflow.runtime.mode=demo",
      "lexiflow.segment-analysis.enabled=true",
      "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-observation-${random.uuid}.jsonl"
    })
class CaptionRequestObservationHttpTest {
  @LocalServerPort private int port;

  @Test
  void mapsHttpOutcomesAndEmitsOneRedactedTerminalPerRequest() throws Exception {
    var logger = (Logger) LoggerFactory.getLogger(StructuredEventLogger.class);
    var appender = new ListAppender<ILoggingEvent>();
    appender.start();
    logger.addAppender(appender);
    try {
      var ok = post(payload(segment("a", "reliable", true), null));
      var noHint = post(payload(segment("b", "zxqv", true), null));
      var noNew = post(payload(segment("c", "reliable", false), null));
      var badJson = post("{");
      var invalidDomain =
          post(payload(segment("d", "valid", true).replace("\"line\":0", "\"line\":-1"), null));
      assertEquals(200, ok.statusCode());
      assertEquals(200, noHint.statusCode());
      assertEquals(200, noNew.statusCode());
      assertEquals(400, badJson.statusCode());
      assertEquals(400, invalidDomain.statusCode());
      for (var response : List.of(ok, noHint, noNew, badJson, invalidDomain)) {
        var id = response.headers().firstValue("X-Request-ID").orElseThrow();
        assertEquals(id, java.util.UUID.fromString(id).toString());
        assertNotEquals("client-forged-id", id);
        assertTrue(
            appender.list.stream()
                .anyMatch(
                    entry ->
                        entry.getFormattedMessage().contains("caption.request.completed")
                            && entry.getFormattedMessage().contains(id)));
      }
      var terminals =
          appender.list.stream()
              .map(ILoggingEvent::getFormattedMessage)
              .filter(line -> line.contains("caption.request.completed"))
              .toList();
      assertEquals(5, terminals.size());
      assertTrue(terminals.stream().anyMatch(line -> line.contains("NO_HINT")));
      assertTrue(terminals.stream().anyMatch(line -> line.contains("NO_NEW_SEGMENTS")));
      assertTrue(terminals.stream().anyMatch(line -> line.contains("INVALID_REQUEST")));
      var successEvent = eventFor(terminals, ok.headers().firstValue("X-Request-ID").orElseThrow());
      assertTrue(successEvent.contains("\"new_ranges\":1"));
      assertTrue(successEvent.contains("\"selected\":1"));
      var unchanged = eventFor(terminals, noNew.headers().firstValue("X-Request-ID").orElseThrow());
      assertFalse(unchanged.contains("lexicon_version"));
      assertFalse(unchanged.contains("\"query\":"));
      assertFalse(unchanged.contains("\"selection\":"));
      assertFalse(unchanged.contains("\"analysis\":"));

      assertTrue(
          eventFor(terminals, noHint.headers().firstValue("X-Request-ID").orElseThrow())
              .contains("\"selected\":0"));
      var all = String.join("\n", terminals);
      assertFalse(all.contains("reliable"));
      assertFalse(all.contains("zxqv"));
      assertFalse(all.contains("client-forged-id"));
      assertFalse(all.contains("CaptionRequestObservationHttpTest"));

      var before = terminals.size();
      var ids = new ArrayList<String>();
      try (var executor = Executors.newFixedThreadPool(6)) {
        var futures =
            IntStream.range(0, 6)
                .mapToObj(
                    index ->
                        executor.submit(
                            () ->
                                post(
                                    payload(
                                        segment(
                                            "parallel-" + index,
                                            index % 3 == 0 ? "reliable" : "zxqv",
                                            index % 3 != 2),
                                        null))))
                .toList();
        for (var future : futures) {
          var response = future.get();
          assertEquals(200, response.statusCode());
          ids.add(response.headers().firstValue("X-Request-ID").orElseThrow());
        }
        assertEquals(6, ids.stream().distinct().count());
        assertTrue(ids.stream().noneMatch("client-forged-id"::equals));
      }
      var after =
          appender.list.stream()
              .map(ILoggingEvent::getFormattedMessage)
              .filter(line -> line.contains("caption.request.completed"))
              .count();
      assertEquals(before + 6, after);
      var concurrentEvents =
          appender.list.stream()
              .map(ILoggingEvent::getFormattedMessage)
              .filter(line -> line.contains("caption.request.completed"))
              .toList();
      for (int index = 0; index < ids.size(); index++) {
        var line = eventFor(concurrentEvents, ids.get(index));
        if (index % 3 == 0) {
          assertTrue(line.contains("\"reason\":\"OK\""));
          assertTrue(line.contains("\"selected\":1"));
          assertTrue(line.contains("\"new_ranges\":1"));
        } else if (index % 3 == 1) {
          assertTrue(line.contains("\"reason\":\"NO_HINT\""));
          assertTrue(line.contains("\"selected\":0"));
          assertTrue(line.contains("\"new_ranges\":1"));
        } else {
          assertTrue(line.contains("\"reason\":\"NO_NEW_SEGMENTS\""));
          assertTrue(line.contains("\"new_ranges\":0"));
        }
      }
    } finally {
      logger.detachAppender(appender);
      appender.stop();
    }
  }

  private HttpResponse<String> post(String body) throws Exception {
    try (var client = HttpClient.newHttpClient()) {
      return client.send(
          HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/api/v1/caption-hints"))
              .header("Content-Type", "application/json")
              .header("X-Request-ID", "client-forged-id")
              .POST(HttpRequest.BodyPublishers.ofString(body))
              .build(),
          HttpResponse.BodyHandlers.ofString());
    }
  }

  private static String eventFor(List<String> events, String requestId) {
    return events.stream().filter(line -> line.contains(requestId)).findFirst().orElseThrow();
  }

  private static String payload(String segment, String prior) {
    return "{\"captionTopicKey\":\"topic\",\"trackKey\":null,\"lastRequestedSnapshot\":"
        + prior
        + ",\"currentSnapshot\":{\"captions\":[{\"windowId\":null,\"startMs\":null,\"segments\":["
        + segment
        + "]}]}}";
  }

  private static String segment(String key, String text, boolean append) {
    return "{\"key\":\""
        + key
        + "\",\"text\":\""
        + text
        + "\",\"offsetMs\":null,\"append\":"
        + append
        + ",\"line\":0}";
  }
}
