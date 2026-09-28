package io.lexiflow.api.runtime;

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
    properties =
        "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-readiness-${random.uuid}.jsonl")
class LexiconReadinessHttpTest {
  @LocalServerPort private int port;

  @Test
  void emptyFormalProcessIsLiveButNotReadyAndRejectsHintsWithoutLeaks() throws Exception {
    try (var client = HttpClient.newHttpClient()) {
      assertEquals(200, get(client, "/actuator/health/liveness").statusCode());
      var readiness = get(client, "/actuator/health/readiness");
      assertEquals(503, readiness.statusCode());
      assertTrue(readiness.body().contains("DEPENDENCY_UNAVAILABLE"));
      assertTrue(readiness.body().contains("formal"));
      assertFalse(readiness.body().contains("\"version\""));
      assertFalse(readiness.body().contains("jdbc"));
      var response =
          client.send(
              HttpRequest.newBuilder(url("/api/v1/caption-hints"))
                  .header("Content-Type", "application/json")
                  .POST(
                      HttpRequest.BodyPublishers.ofString(
                          "{\"captionTopicKey\":\"topic\",\"trackKey\":null,\"lastRequestedSnapshot\":null,\"currentSnapshot\":{\"captions\":[{\"windowId\":null,\"startMs\":null,\"segments\":[{\"key\":\"a\",\"text\":\"reliable\",\"offsetMs\":null,\"append\":true,\"line\":0}]}]}}"))
                  .build(),
              HttpResponse.BodyHandlers.ofString());
      assertEquals(503, response.statusCode());
      assertTrue(response.body().contains("LEXICON_NOT_READY"));
      assertFalse(response.body().contains("reliable"));
    }
  }

  private HttpResponse<String> get(HttpClient client, String path) throws Exception {
    return client.send(
        HttpRequest.newBuilder(url(path)).GET().build(), HttpResponse.BodyHandlers.ofString());
  }

  private URI url(String path) {
    return URI.create("http://127.0.0.1:" + port + path);
  }
}
