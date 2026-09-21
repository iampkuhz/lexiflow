package io.lexiflow.integration;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.lexicon.platform.persistence.PostgresSchemaMigrator;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

/**
 * Exercises the built artifacts only after isolated PostgreSQL and Redis are explicitly supplied.
 */
class SystemRuntimeSmokeTest {
  private static final Duration START_TIMEOUT = Duration.ofSeconds(45);
  private Process api;
  private Process worker;
  private Path apiLog;
  private Path workerLog;
  private String jdbcUrl;
  private String schema;

  @AfterEach
  void cleanUp() throws Exception {
    stop(worker);
    stop(api);
    if (jdbcUrl != null && schema != null) {
      try (var connection = DriverManager.getConnection(jdbcUrl);
          var statement = connection.createStatement()) {
        statement.execute("DROP SCHEMA IF EXISTS \"" + schema + "\" CASCADE");
      }
    }
    if (apiLog != null) Files.deleteIfExists(apiLog);
    if (workerLog != null) Files.deleteIfExists(workerLog);
  }

  @Test
  void verifiesDependencyProtocolsMigrationApiHealthAndWorkerStartup() throws Exception {
    jdbcUrl = requiredProperty("lexiflow.postgres.test.jdbcUrl");
    var redis = endpoint(requiredProperty("lexiflow.redis.test.endpoint"));
    assertRedisPong(redis);

    schema = "lf_runtime_" + UUID.randomUUID().toString().replace("-", "");
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA \"" + schema + "\"");
    }
    var schemaJdbcUrl = jdbcUrl + (jdbcUrl.contains("?") ? "&" : "?") + "currentSchema=" + schema;
    assertEquals(
        List.of(1, 2, 3, 4, 5),
        PostgresSchemaMigrator.apply(
            schemaJdbcUrl, Path.of(requiredProperty("lexiflow.migrations.dir")), null));

    var repository = Path.of(requiredProperty("lexiflow.repository.root"));
    var java = Path.of(System.getProperty("java.home"), "bin", "java").toString();
    var apiJar = bootJar(repository, "api");
    var workerJar = bootJar(repository, "worker");
    var port = freePort();
    apiLog = Files.createTempFile("lexiflow-api-runtime-smoke-", ".log");
    api =
        new ProcessBuilder(
                java,
                "-jar",
                apiJar.toString(),
                "--server.port=" + port,
                "--spring.datasource.url=" + schemaJdbcUrl)
            .directory(repository.toFile())
            .redirectErrorStream(true)
            .redirectOutput(apiLog.toFile())
            .start();
    awaitApiHealth(port, apiLog);

    workerLog = Files.createTempFile("lexiflow-worker-runtime-smoke-", ".log");
    worker =
        new ProcessBuilder(java, "-jar", workerJar.toString())
            .directory(repository.toFile())
            .redirectErrorStream(true)
            .redirectOutput(workerLog.toFile())
            .start();
    awaitWorkerStartup(worker, workerLog);
  }

  private static String requiredProperty(String name) {
    var value = System.getProperty(name, "").strip();
    if (value.isEmpty())
      throw new IllegalStateException("required isolated runtime property is absent: " + name);
    return value;
  }

  private static InetSocketAddress endpoint(String value) {
    var separator = value.lastIndexOf(':');
    if (separator <= 0 || separator == value.length() - 1) {
      throw new IllegalArgumentException("invalid isolated Redis endpoint");
    }
    return new InetSocketAddress(
        value.substring(0, separator), Integer.parseInt(value.substring(separator + 1)));
  }

  private static void assertRedisPong(InetSocketAddress endpoint) throws IOException {
    try (var socket = new Socket()) {
      socket.connect(endpoint, 3_000);
      socket.getOutputStream().write("PING\r\n".getBytes(StandardCharsets.US_ASCII));
      socket.getOutputStream().flush();
      assertEquals(
          "+PONG\r\n",
          new String(socket.getInputStream().readNBytes(7), StandardCharsets.US_ASCII));
    }
  }

  private static Path bootJar(Path repository, String application) throws IOException {
    var directory = repository.resolve("backend/apps").resolve(application).resolve("build/libs");
    try (var files = Files.list(directory)) {
      return files
          .filter(path -> path.getFileName().toString().endsWith(".jar"))
          .filter(path -> !path.getFileName().toString().endsWith("-plain.jar"))
          .findFirst()
          .orElseThrow(() -> new IllegalStateException("boot jar is absent for " + application));
    }
  }

  private static int freePort() throws IOException {
    try (var socket = new ServerSocket(0)) {
      return socket.getLocalPort();
    }
  }

  private void awaitApiHealth(int port, Path log) throws Exception {
    try (var client = HttpClient.newHttpClient()) {
      var deadline = Instant.now().plus(START_TIMEOUT);
      while (Instant.now().isBefore(deadline)) {
        if (!api.isAlive()) throw startupFailure("API", log);
        try {
          var response =
              client.send(
                  HttpRequest.newBuilder(
                          URI.create("http://127.0.0.1:" + port + "/actuator/health"))
                      .timeout(Duration.ofSeconds(2))
                      .GET()
                      .build(),
                  HttpResponse.BodyHandlers.ofString());
          if (response.statusCode() == 200 && response.body().contains("\"UP\"")) return;
        } catch (IOException ignored) {
          // The owned process is still starting.
        }
        Thread.sleep(200);
      }
    }
    throw startupFailure("API", log);
  }

  private void awaitWorkerStartup(Process process, Path log) throws Exception {
    var deadline = Instant.now().plus(Duration.ofSeconds(5));
    while (Instant.now().isBefore(deadline)) {
      if (Files.exists(log) && Files.readString(log).contains("Started WorkerApplication")) return;
      if (!process.isAlive()) {
        Thread.sleep(200);
        if (Files.exists(log) && Files.readString(log).contains("Started WorkerApplication"))
          return;
        throw startupFailure("worker", log);
      }
      Thread.sleep(200);
    }
    throw startupFailure("worker", log);
  }

  private static IllegalStateException startupFailure(String name, Path log) throws IOException {
    var output = Files.exists(log) ? Files.readString(log) : "";
    return new IllegalStateException(
        name
            + " did not reach readiness: "
            + output.substring(Math.max(0, output.length() - 2_000)));
  }

  private static void stop(Process process) throws InterruptedException {
    if (process == null || !process.isAlive()) return;
    process.destroy();
    if (!process.waitFor(10, java.util.concurrent.TimeUnit.SECONDS)) process.destroyForcibly();
  }
}
