package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = "lexiflow.runtime.mode=demo")
class PreferencesHttpTest {
  private static final Path DB =
      Path.of(
          System.getProperty("java.io.tmpdir"),
          "lexiflow-preferences-http-test-" + java.util.UUID.randomUUID() + ".db");
  private static final String ORIGIN = "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk";
  @LocalServerPort int port;

  @BeforeAll
  static void initializeDatabase() throws Exception {
    Files.deleteIfExists(DB);
    String schema =
        Files.readString(Path.of(System.getProperty("lexiflow.preferences.schema.file")));
    try (var c = DriverManager.getConnection("jdbc:sqlite:" + DB);
        var s = c.createStatement()) {
      for (String sql : schema.split(";")) if (!sql.isBlank()) s.execute(sql);
    }
  }

  @AfterAll
  static void removeDatabase() throws Exception {
    Files.deleteIfExists(DB);
    Files.deleteIfExists(Path.of(DB + "-journal"));
  }

  @DynamicPropertySource
  static void preferences(DynamicPropertyRegistry registry) {
    registry.add("lexiflow.preferences.jdbc-url", () -> "jdbc:sqlite:" + DB);
  }

  @Test
  void realHttpPreflightAndRequestAreExactOriginNoStoreAndPersistent() {
    var url = "http://127.0.0.1:" + port + "/api/v1/preferences";
    try (var client = java.net.http.HttpClient.newHttpClient()) {
      var options =
          java.net.http.HttpRequest.newBuilder(java.net.URI.create(url))
              .method("OPTIONS", java.net.http.HttpRequest.BodyPublishers.noBody())
              .header("Origin", ORIGIN)
              .header("Access-Control-Request-Method", "POST")
              .header("Access-Control-Request-Headers", "content-type")
              .build();
      var opt = client.send(options, java.net.http.HttpResponse.BodyHandlers.ofString());
      assertEquals(204, opt.statusCode());
      assertEquals(ORIGIN, opt.headers().firstValue("Access-Control-Allow-Origin").orElse(null));
      assertEquals("no-store", opt.headers().firstValue("Cache-Control").orElse(null));
      var request =
          java.net.http.HttpRequest.newBuilder(java.net.URI.create(url))
              .header("Content-Type", "application/json")
              .header("Origin", ORIGIN)
              .POST(
                  java.net.http.HttpRequest.BodyPublishers.ofString(
                      "{\"action\":\"setCutoff\",\"commonWordCutoff\":1000}"))
              .build();
      var response = client.send(request, java.net.http.HttpResponse.BodyHandlers.ofString());
      assertEquals(200, response.statusCode());
      assertEquals(
          ORIGIN, response.headers().firstValue("Access-Control-Allow-Origin").orElse(null));
      assertEquals("no-store", response.headers().firstValue("Cache-Control").orElse(null));
      assertTrue(response.body().contains("\"commonWordCutoff\":1000"));
      var wrong =
          java.net.http.HttpRequest.newBuilder(java.net.URI.create(url))
              .header("Content-Type", "application/json")
              .header("Origin", "http://localhost")
              .POST(java.net.http.HttpRequest.BodyPublishers.ofString("{\"action\":\"read\"}"))
              .build();
      assertEquals(
          403, client.send(wrong, java.net.http.HttpResponse.BodyHandlers.ofString()).statusCode());
    } catch (Exception e) {
      throw new AssertionError("real preferences HTTP request failed", e);
    }
  }
}
