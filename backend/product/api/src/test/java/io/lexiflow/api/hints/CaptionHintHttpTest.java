package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.core.env.Environment;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {"lexiflow.runtime.mode=demo", "lexiflow.segment-analysis.enabled=true"})
class CaptionHintHttpTest {
  @TempDir static Path tempDir;
  @LocalServerPort private int port;
  @Autowired private Environment environment;

  @DynamicPropertySource
  static void segmentAnalysisProperties(DynamicPropertyRegistry registry) {
    registry.add(
        "lexiflow.segment-analysis.path", () -> tempDir.resolve("segments.jsonl").toString());
  }

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
    var analysisPath = Path.of(environment.getRequiredProperty("lexiflow.segment-analysis.path"));
    assertTrue(Files.isRegularFile(analysisPath));
    assertTrue(Files.size(analysisPath) > 0);
    var unknown = post(payload(segment("a", "zxqv", true)));
    assertEquals(200, unknown.statusCode());
    assertTrue(unknown.body().contains("\"processedKeys\":[\"a\"]"));
    assertTrue(unknown.body().contains("\"hints\":[]"));
    Files.deleteIfExists(analysisPath);
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
      var runtimeStatus =
          client.send(
              HttpRequest.newBuilder(
                      URI.create("http://127.0.0.1:" + port + "/api/v1/runtime-status"))
                  .GET()
                  .build(),
              HttpResponse.BodyHandlers.ofString());
      assertEquals(200, runtimeStatus.statusCode());
      assertTrue(runtimeStatus.body().contains("\"mode\":\"demo\""));
      assertTrue(runtimeStatus.body().contains("\"reason\":\"DEMO_MODE\""));
      assertTrue(runtimeStatus.body().contains("\"ready\":false"));
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
