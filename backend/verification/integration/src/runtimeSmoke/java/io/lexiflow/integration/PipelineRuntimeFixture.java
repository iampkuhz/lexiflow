package io.lexiflow.integration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.platform.persistence.PostgresSchemaInitializer;
import java.io.IOException;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * Owns only this test's random schema, synthetic files, child processes, and loopback HTTP client.
 */
final class PipelineRuntimeFixture implements AutoCloseable {
  private static final Duration PROCESS_TIMEOUT = Duration.ofSeconds(90);
  private static final Duration HTTP_TIMEOUT = Duration.ofSeconds(8);
  private final ObjectMapper json = new ObjectMapper();
  private final String adminJdbcUrl = required("lexiflow.postgres.test.jdbcUrl");
  private final String schema = "lf_pipeline_" + UUID.randomUUID().toString().replace("-", "");
  private final String jdbcUrl;
  private final Path root = Path.of(required("lexiflow.repository.root"));
  private final Path temp = Files.createTempDirectory("lexiflow-pipeline-");
  private final Path analysis = temp.resolve("analysis.jsonl");
  private final Path apiLog = temp.resolve("api.log");
  private final HttpClient client = HttpClient.newBuilder().connectTimeout(HTTP_TIMEOUT).build();
  private Process api;
  private int port;

  PipelineRuntimeFixture() throws Exception {
    jdbcUrl = adminJdbcUrl + (adminJdbcUrl.contains("?") ? "&" : "?") + "currentSchema=" + schema;
    try {
      try (var connection = DriverManager.getConnection(adminJdbcUrl);
          var statement = connection.createStatement()) {
        statement.execute("CREATE SCHEMA \"" + schema + "\"");
      }
      PostgresSchemaInitializer.initialize(
          jdbcUrl, Path.of(required("lexiflow.postgres.schema.file")));
    } catch (Exception | Error failure) {
      cleanupAfterConstructionFailure(failure);
      throw failure;
    }
  }

  Path temp() {
    return temp;
  }

  String jdbcUrl() {
    return jdbcUrl;
  }

  void publish(Path csv) throws Exception {
    runCli(csv, true);
  }

  void publishExpectFailure(Path csv) throws Exception {
    var result = runCli(csv, false);
    assertTrue(result.exitCode() != 0, "constraint-violating publication unexpectedly succeeded");
    assertTrue(
        result.output().contains("synthetic_third_rejected")
            && result.output().toLowerCase(java.util.Locale.ROOT).contains("check constraint"),
        "failure was not the expected real PostgreSQL CHECK violation: " + result.output());
    var events = importEvents(result.output());
    assertTrue(
        events.stream()
            .anyMatch(
                event ->
                    "lexicon.import.completed".equals(event.path("event").stringValue())
                        && "PUBLISH_ROLLED_BACK".equals(event.path("reason").stringValue())),
        "failed import lacks readable PUBLISH_ROLLED_BACK terminal: " + result.output());
  }

  private ProcessResult runCli(Path csv, boolean success) throws Exception {
    var cp = required("lexiflow.runtimeSmoke.classpath");
    var command =
        List.of(
            java(),
            "-cp",
            cp,
            "io.lexiflow.lexicon.platform.importer.LexiconImportMain",
            "publish",
            "--input",
            csv.toString(),
            "--database-url",
            jdbcUrl,
            "--batch-source-id",
            "synthetic-pipeline",
            "--batch-license-id",
            "CC0");
    var log = temp.resolve("import-" + UUID.randomUUID() + ".log");
    Process process = null;
    try {
      process =
          new ProcessBuilder(command)
              .directory(root.toFile())
              .redirectErrorStream(true)
              .redirectOutput(log.toFile())
              .start();
      if (!process.waitFor(PROCESS_TIMEOUT.toSeconds(), TimeUnit.SECONDS)) {
        throw new AssertionError("LexiconImportMain timed out after " + PROCESS_TIMEOUT);
      }
      var output = Files.readString(log);
      if (success) assertEquals(0, process.exitValue(), output);
      return new ProcessResult(process.exitValue(), output);
    } finally {
      stop(process);
      Files.deleteIfExists(log);
    }
  }

  void startApi() throws Exception {
    if (api != null && api.isAlive()) return;
    port = freePort();
    var bootJar = Path.of(required("lexiflow.runtimeSmoke.bootJar"));
    api =
        new ProcessBuilder(
                java(),
                "-jar",
                bootJar.toString(),
                "--lexiflow.runtime.mode=formal",
                "--lexiflow.segment-analysis.path=" + analysis,
                "--lexiflow.segment-analysis.console=false",
                "--logging.level.io.lexiflow.observability.platform.StructuredEventLogger=DEBUG",
                "--server.address=127.0.0.1",
                "--server.port=" + port,
                "--spring.datasource.url=" + jdbcUrl)
            .directory(root.toFile())
            .redirectErrorStream(true)
            .redirectOutput(apiLog.toFile())
            .start();
    var deadline = Instant.now().plusSeconds(50);
    while (Instant.now().isBefore(deadline)) {
      if (!api.isAlive()) throw new AssertionError("API exited: " + tail(apiLog));
      try {
        var response = get("/actuator/health/liveness");
        if (response.statusCode() == 200) {
          var ready = get("/actuator/health/readiness");
          if (ready.statusCode() == 200) return;
        }
      } catch (IOException ignored) {
      }
      Thread.sleep(150);
    }
    throw new AssertionError("formal API did not become ready: " + tail(apiLog));
  }

  HttpResponse<String> get(String path) throws Exception {
    return client.send(
        HttpRequest.newBuilder(uri(path)).timeout(HTTP_TIMEOUT).GET().build(),
        HttpResponse.BodyHandlers.ofString());
  }

  JsonNode post(String text) throws Exception {
    return postTracked(text).body();
  }

  TrackedResponse postTracked(String text) throws Exception {
    var response =
        client.send(
            HttpRequest.newBuilder(uri("/api/v1/caption-hints"))
                .timeout(HTTP_TIMEOUT)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(text))
                .build(),
            HttpResponse.BodyHandlers.ofString());
    assertEquals(200, response.statusCode(), response.body());
    var requestId = response.headers().firstValue("X-Request-ID").orElseThrow();
    UUID.fromString(requestId);
    return new TrackedResponse(json.readTree(response.body()), requestId);
  }

  JsonNode postText(String key, String text) throws Exception {
    return post(PipelineSyntheticSources.caption(key, text, true));
  }

  JsonNode postIncremental() throws Exception {
    var body =
        """
        {"captionTopicKey":"synthetic-incremental","trackKey":null,
         "lastRequestedSnapshot":{"captions":[{"windowId":null,"startMs":null,"segments":[
           {"key":"old","text":"unchanged","offsetMs":null,"append":false,"line":0}]}]},
         "currentSnapshot":{"captions":[{"windowId":null,"startMs":null,"segments":[
           {"key":"old","text":"unchanged","offsetMs":null,"append":false,"line":0},
           {"key":"new","text":"😀 dependable","offsetMs":null,"append":true,"line":1}]}]}}
        """;
    return post(body);
  }

  long publishedVersion() throws Exception {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement();
        var rs =
            statement.executeQuery(
                "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id=1")) {
      assertTrue(rs.next(), "dataset row absent");
      return rs.getLong(1);
    }
  }

  List<String> databaseSnapshot() throws Exception {
    var snapshot = new java.util.ArrayList<String>();
    for (var table : List.of("lexicon_dataset", "lexicon_entry", "lexicon_form")) {
      try (var connection = DriverManager.getConnection(jdbcUrl);
          var statement = connection.createStatement();
          var rs =
              statement.executeQuery(
                  "SELECT to_jsonb(t)::text FROM " + table + " t ORDER BY to_jsonb(t)::text")) {
        while (rs.next()) snapshot.add(table + ":" + rs.getString(1));
      }
    }
    return List.copyOf(snapshot);
  }

  JsonNode awaitRequestEvent(String requestId) throws Exception {
    var deadline = Instant.now().plusSeconds(3);
    while (Instant.now().isBefore(deadline)) {
      for (var line : (Files.exists(apiLog) ? Files.readString(apiLog) : "").lines().toList()) {
        var event = readableRequestEvent(line);
        if (event != null && requestId.equals(event.path("request_id").stringValue())) return event;
      }
      Thread.sleep(40);
    }
    throw new AssertionError("no terminal request event for HTTP X-Request-ID=" + requestId);
  }

  void prohibitThirdLemma() throws Exception {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute(
          "ALTER TABLE lexicon_entry ADD CONSTRAINT synthetic_third_rejected CHECK (lemma <> 'thirdversion')");
    }
  }

  @Override
  public void close() {
    Throwable failure = null;
    try {
      stop(api);
    } catch (Throwable problem) {
      failure = problem;
    }
    try {
      client.close();
    } catch (Throwable problem) {
      failure = combine(failure, problem);
    }
    try {
      dropSchema();
    } catch (Throwable problem) {
      failure = combine(failure, problem);
    }
    try {
      deleteTree(temp);
    } catch (Throwable problem) {
      failure = combine(failure, problem);
    }
    if (failure != null)
      throw new IllegalStateException("failed to clean runtime fixture", failure);
  }

  private void cleanupAfterConstructionFailure(Throwable original) {
    try {
      client.close();
    } catch (Throwable cleanupFailure) {
      original.addSuppressed(cleanupFailure);
    }
    try {
      dropSchema();
    } catch (Throwable cleanupFailure) {
      original.addSuppressed(cleanupFailure);
    }
    try {
      deleteTree(temp);
    } catch (Throwable cleanupFailure) {
      original.addSuppressed(cleanupFailure);
    }
  }

  private void dropSchema() throws Exception {
    try (var connection = DriverManager.getConnection(adminJdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("DROP SCHEMA IF EXISTS \"" + schema + "\" CASCADE");
    }
  }

  private static void deleteTree(Path path) throws IOException {
    if (!Files.exists(path)) return;
    try (var files = Files.walk(path)) {
      for (var file : files.sorted(java.util.Comparator.reverseOrder()).toList()) {
        Files.deleteIfExists(file);
      }
    }
  }

  private static Throwable combine(Throwable existing, Throwable next) {
    if (existing == null) return next;
    existing.addSuppressed(next);
    return existing;
  }

  private URI uri(String path) {
    return URI.create("http://127.0.0.1:" + port + path);
  }

  private static String java() {
    return Path.of(System.getProperty("java.home"), "bin", "java").toString();
  }

  private static String required(String name) {
    var value = System.getProperty(name, "").strip();
    if (value.isEmpty())
      throw new IllegalStateException("required isolated runtime property absent: " + name);
    return value;
  }

  private static int freePort() throws IOException {
    try (var socket = new ServerSocket(0)) {
      return socket.getLocalPort();
    }
  }

  private static void stop(Process process) {
    if (process == null || !process.isAlive()) return;
    process.destroy();
    try {
      if (!process.waitFor(8, TimeUnit.SECONDS)) {
        process.destroyForcibly();
        if (!process.waitFor(5, TimeUnit.SECONDS)) {
          throw new IllegalStateException("owned child process survived forced termination");
        }
      }
    } catch (InterruptedException interrupted) {
      process.destroyForcibly();
      Thread.currentThread().interrupt();
      throw new IllegalStateException("interrupted while stopping owned process", interrupted);
    }
  }

  private static String tail(Path path) throws IOException {
    if (!Files.exists(path)) return "(no API log)";
    var value = Files.readString(path);
    return value.substring(Math.max(0, value.length() - 3_000));
  }

  private record ProcessResult(int exitCode, String output) {}

  record TrackedResponse(JsonNode body, String requestId) {}

  private List<JsonNode> importEvents(String output) {
    var result = new java.util.ArrayList<JsonNode>();
    for (var line : output.lines().toList()) {
      var columns = line.split("\\|", 6);
      if (columns.length < 5) continue;
      boolean diagnostic = "ERROR".equals(columns[1]) || "WARN".equals(columns[1]);
      if (!diagnostic && !"INFO".equals(columns[1])) continue;
      int eventColumn = diagnostic ? 3 : 2;
      if (columns.length != (diagnostic ? 6 : 5)
          || !columns[eventColumn].startsWith("lexicon.import.")) continue;
      var event = json.createObjectNode();
      event.put("event", columns[eventColumn]);
      for (var field : columns[eventColumn + 1].split(";")) {
        if (field.startsWith("reason=")) event.put("reason", field.substring("reason=".length()));
      }
      result.add(event);
    }
    return List.copyOf(result);
  }

  private JsonNode readableRequestEvent(String line) {
    var columns = line.split("\\|", 6);
    if (columns.length != 6 || !"DEBUG".equals(columns[1])) return null;
    try {
      UUID.fromString(columns[2]);
    } catch (IllegalArgumentException invalidId) {
      return null;
    }
    if (!"caption.request.completed".equals(columns[3])) return null;
    var event = json.createObjectNode();
    event.put("request_id", columns[2]);
    event.put("event", columns[3]);
    var counts = event.putObject("counts");
    for (var field : columns[4].split(";")) {
      var separator = field.indexOf('=');
      if (separator < 0) continue;
      var key = field.substring(0, separator);
      var value = field.substring(separator + 1);
      if ("lexicon_version".equals(key)) event.put(key, Long.parseLong(value));
      else if (key.startsWith("count_"))
        counts.put(key.substring("count_".length()), Long.parseLong(value));
    }
    return event;
  }
}
