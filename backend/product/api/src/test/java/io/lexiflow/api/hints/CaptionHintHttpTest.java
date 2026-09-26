package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class CaptionHintHttpTest {
  @LocalServerPort private int port;

  @Test
  void returnsReadyAndNoPendingAcrossTheRealHttpBoundary() throws Exception {
    var known = post(payload("reliable", 8));
    assertEquals(200, known.statusCode());
    assertEquals("no-store", known.headers().firstValue("Cache-Control").orElseThrow());
    assertTrue(
        known
            .headers()
            .firstValue("Server-Timing")
            .orElseThrow()
            .matches(
                "query;dur=[0-9]+[.][0-9]{3}, rules;dur=[0-9]+[.][0-9]{3}, api;dur=[0-9]+[.][0-9]{3}"));
    assertTrue(known.body().contains("\"state\":\"READY\""));
    assertTrue(known.body().contains("可靠的"));
    assertTrue(known.body().contains("\"caption\":\"reliable\""));
    assertTrue(
        known
            .body()
            .contains(
                "\"senseId\":\""
                    + UUID.nameUUIDFromBytes("sense:reliable".getBytes(StandardCharsets.UTF_8))
                    + "\""));
    var unknown = post(payload("zxqv", 4));
    assertEquals(200, unknown.statusCode());
    assertTrue(unknown.body().contains("\"state\":\"NO_PENDING\""));
  }

  @Test
  void rejectsMalformedJsonAndOutOfContractRequestsWith400() throws Exception {
    for (var body :
        new String[] {
          "{",
          "null",
          "{}",
          payload("reliable", 9),
          payload("", 0),
          payload("x".repeat(501), 501),
          payload("reliable", 8).replace("\"contentRevision\":1", "\"contentRevision\":0"),
          payload("reliable", 8).replace("00000000-0000-0000-0000-000000000001", "bad")
        }) {
      assertEquals(400, post(body).statusCode());
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

  private static String payload(String caption, int end) {
    return "{\"contentId\":\"00000000-0000-0000-0000-000000000001\",\"contentRevision\":1,\"segmentId\":\""
        + "a".repeat(64)
        + "\",\"caption\":\""
        + caption
        + "\",\"startOffset\":0,\"endOffset\":"
        + end
        + "}";
  }
}
