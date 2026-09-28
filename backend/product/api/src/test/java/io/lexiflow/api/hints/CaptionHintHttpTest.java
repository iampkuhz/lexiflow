package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "lexiflow.runtime.mode=demo",
      "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-caption-test-${random.uuid}.jsonl"
    })
class CaptionHintHttpTest {
  @LocalServerPort private int port;

  @Test
  void returnsKeyedHintsAndNoPendingAcrossHttp() throws Exception {
    var known = post(payload(segment("a", "reliable", true)));
    assertEquals(200, known.statusCode());
    assertEquals("no-store", known.headers().firstValue("Cache-Control").orElseThrow());
    assertTrue(known.headers().firstValue("Server-Timing").orElseThrow().contains("query;dur="));
    assertTrue(known.body().contains("\"processedKeys\":[\"a\"]"));
    assertTrue(known.body().contains("\"startKey\":\"a\""));
    assertTrue(known.body().contains("可靠的"));
    assertFalse(known.body().contains("\"caption\""));
    var unknown = post(payload(segment("a", "zxqv", true)));
    assertEquals(200, unknown.statusCode());
    assertTrue(unknown.body().contains("\"processedKeys\":[\"a\"]"));
    assertTrue(unknown.body().contains("\"hints\":[]"));
  }

  @Test
  void rejectsMalformedLegacyDuplicateAndExcessiveRequests() throws Exception {
    var legacy = "{\"contentId\":\"old\",\"caption\":\"reliable\"}";
    var duplicate = payload(segment("a", "one", true) + "," + segment("a", "two", true));
    for (var body :
        new String[] {
          "{",
          "null",
          "{}",
          legacy,
          duplicate,
          payload(segment("a", "x".repeat(501), true)),
          payload(segment("a", "x".repeat(300), true) + "," + segment("b", "y".repeat(201), true)),
          payload(segment("a", "", true)),
          payload(segment("a", "valid", true)).replace("\"append\":true,", ""),
          payload(segment("a", "valid", true)).replace("\"line\":0", "\"line\":-1"),
          payload(segment("a", "valid", true)).replace("\"line\":0", "\"line\":9007199254740992"),
          payload(segment("a", "valid", true))
              .replace("\"captionTopicKey\":\"topic\"", "\"captionTopicKey\":\"\""),
          payload(segment("a", "valid", true))
              .replace("\"windowId\":null", "\"windowId\":\"" + "x".repeat(129) + "\""),
          payload(segment("a", "valid", true))
              .replace("\"key\":\"a\"", "\"key\":\"" + "x".repeat(129) + "\""),
          payload(segment("a", "valid", true))
              .replace("\"append\":true", "\"append\":true,\"contentId\":\"old\"")
        }) {
      assertEquals(400, post(body).statusCode(), body);
    }
  }

  @Test
  void explicitDemoDoesNotAdvertiseFormalReadiness() throws Exception {
    try (var client = HttpClient.newHttpClient()) {
      var readiness =
          client.send(
              HttpRequest.newBuilder(
                      URI.create("http://127.0.0.1:" + port + "/actuator/health/readiness"))
                  .GET()
                  .build(),
              HttpResponse.BodyHandlers.ofString());
      assertEquals(503, readiness.statusCode());
      assertTrue(readiness.body().contains("DEMO_MODE"));
      assertTrue(readiness.body().contains("demo"));
    }
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
    return "{\"key\":\""
        + key
        + "\",\"text\":\""
        + text
        + "\",\"offsetMs\":null,\"append\":"
        + append
        + ",\"line\":0}";
  }
}
