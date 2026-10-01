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
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.core.env.Environment;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/** 验证默认禁用时实际提示不会产生敏感文件或专用控制台输出。 */
@ExtendWith(OutputCaptureExtension.class)
@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {"lexiflow.runtime.mode=demo", "lexiflow.segment-analysis.console=true"})
class SegmentAnalysisDisabledHttpTest {
  @TempDir static Path tempDir;
  @LocalServerPort private int port;
  @Autowired private Environment environment;

  @DynamicPropertySource
  static void segmentAnalysisProperties(DynamicPropertyRegistry registry) {
    registry.add(
        "lexiflow.segment-analysis.path", () -> tempDir.resolve("segments.jsonl").toString());
  }

  @Test
  void disabledLedgerDoesNotWriteOrPrintAndDoesNotChangeHintResponse(CapturedOutput captured)
      throws Exception {
    var path = Path.of(environment.getRequiredProperty("lexiflow.segment-analysis.path"));
    HttpResponse<String> response;
    try (var client = HttpClient.newHttpClient()) {
      response =
          client.send(
              HttpRequest.newBuilder(
                      URI.create("http://127.0.0.1:" + port + "/api/v1/caption-hints"))
                  .header("Content-Type", "application/json")
                  .POST(
                      HttpRequest.BodyPublishers.ofString(
                          "{\"captionTopicKey\":\"topic\",\"trackKey\":null,\"lastRequestedSnapshot\":null,\"currentSnapshot\":{\"captions\":[{\"windowId\":null,\"startMs\":null,\"segments\":[{\"key\":\"a\",\"text\":\"reliable\",\"offsetMs\":null,\"append\":true,\"line\":0}]}]}}"))
                  .build(),
              HttpResponse.BodyHandlers.ofString());
    }
    assertEquals(200, response.statusCode());
    assertTrue(response.body().contains("可靠的"));
    assertFalse(Files.exists(path));
    assertFalse(captured.getOut().contains("[LexiFlow segment]"));
  }
}
