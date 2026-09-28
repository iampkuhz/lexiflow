package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.observability.platform.StructuredEventLogger;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.context.annotation.Primary;

/** 在真实 HTTP 链路验证日志 Sink 故障不改变产品响应。 */
@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "lexiflow.runtime.mode=demo",
      "lexiflow.segment-analysis.path=${java.io.tmpdir}/lexiflow-sink-failure-${random.uuid}.jsonl"
    })
@org.springframework.test.context.ActiveProfiles("caption-request-sink-failure-test")
@Import(CaptionRequestSinkFailureHttpTest.TestBeans.class)
class CaptionRequestSinkFailureHttpTest {
  @LocalServerPort private int port;

  @Test
  void eventSinkFailureDoesNotChangeRealHttpResponse() throws Exception {
    var response =
        post(
            "{\"captionTopicKey\":\"topic\",\"trackKey\":null,\"lastRequestedSnapshot\":null,\"currentSnapshot\":{\"captions\":[{\"windowId\":null,\"startMs\":null,\"segments\":[{\"key\":\"sink\",\"text\":\"reliable\",\"offsetMs\":null,\"append\":true,\"line\":0}]}]}}");
    assertEquals(200, response.statusCode());
    assertTrue(response.body().contains("可靠的"));
    assertTrue(response.headers().firstValue("X-Request-ID").isPresent());
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

  @TestConfiguration(proxyBeanMethods = false)
  @org.springframework.context.annotation.Profile("caption-request-sink-failure-test")
  static class TestBeans {
    @Bean
    @Primary
    StructuredEventLogger failingStructuredEventLogger() {
      return new StructuredEventLogger(
          (level, json) -> {
            throw new IllegalStateException("private-sink-failure");
          });
    }
  }
}
