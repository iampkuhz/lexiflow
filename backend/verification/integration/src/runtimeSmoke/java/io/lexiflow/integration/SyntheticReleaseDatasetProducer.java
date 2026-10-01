package io.lexiflow.integration;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.sql.DriverManager;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** Produces two real, synthetic-only published dataset archives for release runtime checks. */
public final class SyntheticReleaseDatasetProducer {
  private static final Duration PROCESS_TIMEOUT = Duration.ofSeconds(120);
  private static final int PROCESS_OUTPUT_LIMIT = 64 * 1024;
  private static final ObjectMapper JSON = new ObjectMapper();

  private SyntheticReleaseDatasetProducer() {}

  /** Runs the bounded producer from the Gradle JavaExec entry point. */
  public static void main(String[] args) throws Exception {
    if (args.length != 0) throw new IllegalArgumentException("unexpected arguments");
    var output = Path.of(required("lexiflow.release.fixture.output"));
    produce(output);
  }

  static void produce(Path requestedOutput) throws Exception {
    produce(requestedOutput, jdbcUrl -> {});
  }

  static void produce(Path requestedOutput, AfterPublication beforeExport) throws Exception {
    Preflight preflight = preflight(requestedOutput);
    Path output = validateOutput(requestedOutput, preflight.repositoryRoot());
    boolean success = false;
    try {
      Map<String, Object> result;
      try (var fixture = new PipelineRuntimeFixture()) {
        var approvals = fixture.temp().toRealPath().resolve("approvals.json");
        var firstCsv = PipelineSyntheticSources.canonical(fixture.temp(), "release-a");
        var secondCsv = PipelineSyntheticSources.canonical(fixture.temp(), "release-b");
        fixture.publish(firstCsv);
        beforeExport.run(fixture.jdbcUrl());
        Files.writeString(approvals, approval(publicationSources(fixture.jdbcUrl())));
        var a = exportAndVerify(fixture, output.resolve("dataset-a.zip"), approvals);
        fixture.publish(secondCsv);
        beforeExport.run(fixture.jdbcUrl());
        Files.writeString(approvals, approval(publicationSources(fixture.jdbcUrl())));
        var b = exportAndVerify(fixture, output.resolve("dataset-b.zip"), approvals);
        if (a.version() == b.version() || a.sha256().equals(b.sha256())) {
          throw new IOException("synthetic generations were not distinct");
        }
        result =
            Map.of(
                "schemaVersion",
                1,
                "synthetic",
                true,
                "formalReleaseEligible",
                false,
                "datasets",
                List.of(record("dataset-a.zip", a), record("dataset-b.zip", b)));
      }
      // The fixture close above confirms child/schema/temp cleanup before success is published.
      try (var stream =
          Files.newOutputStream(
              output.resolve("result.json"),
              StandardOpenOption.CREATE_NEW,
              StandardOpenOption.WRITE)) {
        stream.write((JSON.writeValueAsString(result) + "\n").getBytes(StandardCharsets.UTF_8));
      }
      success = true;
    } finally {
      if (!success) Files.deleteIfExists(output.resolve("result.json"));
    }
  }

  private static Map<String, Object> record(String name, PackageResult value) throws IOException {
    return Map.of(
        "file", name,
        "bytes", Files.size(value.path()),
        "sha256", value.sha256(),
        "datasetVersion", value.version(),
        "preparationPolicy", value.preparationPolicy());
  }

  private static PackageResult exportAndVerify(
      PipelineRuntimeFixture fixture, Path archive, Path approvals) throws Exception {
    var env = Map.of("LEXIFLOW_RELEASE_JDBC_URL", fixture.jdbcUrl());
    JsonNode export =
        runApi(
            List.of(
                "--release-dataset",
                "export",
                "--output",
                archive.toString(),
                "--approval-file",
                approvals.toString()),
            env,
            Set.of("result", "sha256", "datasetVersion"));
    String hash = export.path("sha256").asString();
    long version = export.path("datasetVersion").asLong();
    if (!hash.matches("[0-9a-f]{64}") || version < 1 || !hash.equals(sha256(archive)))
      throw new IOException("export response did not bind the produced archive");
    runApi(
        List.of(
            "--release-dataset",
            "verify",
            "--package",
            archive.toString(),
            "--expected-sha256",
            hash),
        Map.of(),
        Set.of("result"));
    String policy;
    try (var connection = DriverManager.getConnection(fixture.jdbcUrl());
        var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT preparation_policy FROM lexicon_dataset WHERE dataset_id=1")) {
      if (!rows.next()) throw new IOException("published dataset absent");
      policy = rows.getString(1);
    }
    if (!"lexiflow.deterministic-preparation.v1".equals(policy))
      throw new IOException("unexpected policy");
    return new PackageResult(archive, hash, version, policy);
  }

  private static JsonNode runApi(
      List<String> args, Map<String, String> extraEnv, Set<String> expectedFields)
      throws Exception {
    var bootJar = Path.of(required("lexiflow.runtimeSmoke.bootJar"));
    var command = new java.util.ArrayList<String>();
    command.add(Path.of(System.getProperty("java.home"), "bin", "java").toString());
    command.add("-jar");
    command.add(bootJar.toString());
    command.addAll(args);
    Path stdoutLog = Files.createTempFile("lexiflow-release-command-", ".stdout");
    Path stderrLog = Files.createTempFile("lexiflow-release-command-", ".stderr");
    Process process = null;
    Thread stdoutReader = null;
    Thread stderrReader = null;
    var stdout = new BoundedCapture(PROCESS_OUTPUT_LIMIT);
    var stderr = new BoundedCapture(PROCESS_OUTPUT_LIMIT);
    try {
      var builder =
          new ProcessBuilder(command)
              .directory(Path.of(required("lexiflow.repository.root")).toFile());
      builder.environment().clear();
      builder.environment().putAll(extraEnv);
      process = builder.start();
      stdoutReader = drain(process.getInputStream(), stdout);
      stderrReader = drain(process.getErrorStream(), stderr);
      if (!process.waitFor(PROCESS_TIMEOUT.toSeconds(), TimeUnit.SECONDS))
        throw new IOException("release command timed out");
      stdoutReader.join(5000);
      stderrReader.join(5000);
      if (stdoutReader.isAlive() || stderrReader.isAlive())
        throw new IOException("command output reader did not stop");
      Files.write(stdoutLog, stdout.bytes());
      Files.write(stderrLog, stderr.bytes());
      if (stdout.failed() || stderr.failed())
        throw new IOException("failed to collect command output");
      if (process.exitValue() != 0) throw new IOException("release command failed");
      if (stdout.truncated() || stderr.truncated())
        throw new IOException("release command output exceeded limit");
      String text = new String(stdout.bytes(), StandardCharsets.UTF_8).strip();
      if (text.isEmpty()
          || text.lines().count() != 1
          || !text.startsWith("{")
          || !text.endsWith("}"))
        throw new IOException("release command output violated fixed JSON protocol");
      JsonNode response = JSON.readTree(text);
      var fields = new HashSet<String>();
      fields.addAll(response.propertyNames());
      if (!response.isObject()
          || !fields.equals(expectedFields)
          || !JSON.writeValueAsString(response).equals(text)
          || !response.path("result").isString()
          || !"PASS".equals(response.path("result").asString()))
        throw new IOException("release command result violated fixed protocol");
      if (expectedFields.contains("sha256")
          && (!response.path("sha256").isString()
              || !response.path("sha256").asString().matches("[0-9a-f]{64}")
              || !response.path("datasetVersion").isIntegralNumber()
              || response.path("datasetVersion").asLong() < 1))
        throw new IOException("export result has invalid hash or dataset version");
      return response;
    } finally {
      stop(process);
      Files.deleteIfExists(stdoutLog);
      Files.deleteIfExists(stderrLog);
    }
  }

  private static Thread drain(InputStream input, BoundedCapture capture) {
    Thread reader =
        new Thread(
            () -> {
              try (input) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.read(buffer)) >= 0) capture.accept(buffer, count);
              } catch (IOException ignored) {
                capture.markFailure();
              }
            },
            "synthetic-release-output-reader");
    reader.setDaemon(true);
    reader.start();
    return reader;
  }

  private static List<Map<String, Object>> publicationSources(String jdbcUrl) throws Exception {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT source_manifest FROM lexicon_dataset WHERE dataset_id=1")) {
      if (!rows.next()) throw new IOException("synthetic publication source absent");
      JsonNode manifest = JSON.readTree(rows.getString(1));
      if (!manifest.isArray() || manifest.isEmpty())
        throw new IOException("invalid source manifest");
      List<Map<String, Object>> sources = new ArrayList<>();
      for (JsonNode source : manifest) {
        String id = source.path("source_id").asString();
        String license = source.path("license_id").asString();
        String digest = source.path("source_digest").asString();
        if (id.isBlank() || license.isBlank() || !digest.matches("[0-9a-f]{64}"))
          throw new IOException("invalid published synthetic source");
        sources.add(Map.of("sourceId", id, "licenseId", license, "sourceDigest", digest));
      }
      return List.copyOf(sources);
    }
  }

  private static String approval(List<Map<String, Object>> sources) throws IOException {
    List<Map<String, Object>> approvals =
        sources.stream()
            .map(
                source ->
                    Map.<String, Object>of(
                        "sourceId",
                        source.get("sourceId"),
                        "sourceDigest",
                        source.get("sourceDigest"),
                        "licenseId",
                        source.get("licenseId"),
                        "redistributionApproved",
                        true,
                        "evidenceUrl",
                        "https://example.invalid/synthetic"))
            .toList();
    return JSON.writeValueAsString(Map.of("schemaVersion", 1, "sources", approvals));
  }

  private static Path validateOutput(Path requested, Path repositoryRoot) throws IOException {
    if (!requested.isAbsolute()) throw new IOException("output must be absolute");
    Path output = requested.normalize();
    Path parent = output.getParent();
    if (output.startsWith(repositoryRoot))
      throw new IOException("output must be outside the repository");
    if (parent == null
        || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS)
        || Files.isSymbolicLink(parent)
        || !parent.toRealPath().equals(parent)
        || Files.exists(output, LinkOption.NOFOLLOW_LINKS))
      throw new IOException("output path is not an available dedicated directory");
    Path created = Files.createDirectory(output);
    if (Files.isSymbolicLink(created)
        || !created.toRealPath(LinkOption.NOFOLLOW_LINKS).equals(created))
      throw new IOException("unsafe output directory");
    return created;
  }

  private static Preflight preflight(Path output) throws IOException {
    required("lexiflow.postgres.test.jdbcUrl");
    Path root = Path.of(required("lexiflow.repository.root")).toRealPath();
    Path schema = Path.of(required("lexiflow.postgres.schema.file"));
    Path bootJar = Path.of(required("lexiflow.runtimeSmoke.bootJar"));
    required("lexiflow.runtimeSmoke.classpath");
    if (!Files.isRegularFile(schema, LinkOption.NOFOLLOW_LINKS)
        || !Files.isRegularFile(bootJar, LinkOption.NOFOLLOW_LINKS))
      throw new IOException("required schema or API boot JAR is unavailable");
    if (!output.isAbsolute()) throw new IOException("output must be absolute");
    return new Preflight(root);
  }

  private static String sha256(Path path) throws Exception {
    var digest = MessageDigest.getInstance("SHA-256");
    try (var input = Files.newInputStream(path)) {
      byte[] buffer = new byte[65536];
      int count;
      while ((count = input.read(buffer)) >= 0) digest.update(buffer, 0, count);
    }
    return java.util.HexFormat.of().formatHex(digest.digest());
  }

  private static void stop(Process process) throws InterruptedException {
    if (process == null || !process.isAlive()) return;
    process.destroy();
    if (!process.waitFor(8, TimeUnit.SECONDS)) {
      process.destroyForcibly();
      if (!process.waitFor(5, TimeUnit.SECONDS))
        throw new IllegalStateException("owned process survived termination");
    }
  }

  private static String required(String key) {
    String value = System.getProperty(key, "").strip();
    if (value.isEmpty())
      throw new IllegalStateException("required producer property absent: " + key);
    return value;
  }

  private record PackageResult(Path path, String sha256, long version, String preparationPolicy) {}

  private record Preflight(Path repositoryRoot) {}

  /** 在自有 schema 发布后注入测试故障，不参与生产者正常路径。 */
  @FunctionalInterface
  interface AfterPublication {
    void run(String jdbcUrl) throws Exception;
  }

  /** 持续排空子进程流，只保留固定上限字节并记录截断或读取失败。 */
  private static final class BoundedCapture {
    private final int limit;
    private final ByteArrayOutputStream output = new ByteArrayOutputStream();
    private boolean truncated;
    private boolean failed;

    private BoundedCapture(int limit) {
      this.limit = limit;
    }

    synchronized void accept(byte[] bytes, int count) {
      int keep = Math.min(count, Math.max(0, limit - output.size()));
      output.write(bytes, 0, keep);
      if (keep != count) truncated = true;
    }

    synchronized byte[] bytes() {
      return output.toByteArray();
    }

    synchronized boolean truncated() {
      return truncated;
    }

    synchronized boolean failed() {
      return failed;
    }

    synchronized void markFailure() {
      failed = true;
    }
  }
}
