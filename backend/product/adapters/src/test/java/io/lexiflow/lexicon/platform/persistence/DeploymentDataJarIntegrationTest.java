package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.DriverManager;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.zip.ZipFile;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 仅在 Hook 隔离 PG17 上执行真实 API JAR 的部署数据闭环。 */
@Tag("postgres")
class DeploymentDataJarIntegrationTest {
  @TempDir Path temporary;

  private String cleanupJdbcUrl;
  private String createdRole;
  private boolean databaseCreated;

  @AfterEach
  void cleanupOwnedDatabaseAndRole() throws Exception {
    if (cleanupJdbcUrl == null) {
      return;
    }
    try (var admin = DriverManager.getConnection(cleanupJdbcUrl);
        var statement = admin.createStatement()) {
      if (databaseCreated) {
        try (var owner =
            admin.prepareStatement(
                "SELECT pg_get_userbyid(datdba) = ? FROM pg_database WHERE datname = 'lexiflow'")) {
          owner.setString(1, createdRole);
          try (var rows = owner.executeQuery()) {
            if (!rows.next() || !rows.getBoolean(1)) {
              throw new AssertionError("lexiflow database ownership changed; preserving database");
            }
          }
        }
        statement.execute("DROP DATABASE lexiflow");
      }
      if (createdRole != null) {
        statement.execute("DROP ROLE " + createdRole);
      }
    }
  }

  @Test
  void realJarInspectApplyReuseRebuildAndProtectsExternalView() throws Exception {
    String leaseUrl = System.getProperty("lexiflow.postgres.test.jdbcUrl");
    String jar = System.getProperty("lexiflow.release.test.bootJar");
    assertTrue(leaseUrl != null && !leaseUrl.isBlank());
    assertTrue(jar != null && !jar.isBlank());
    var origin = java.net.URI.create(leaseUrl.substring("jdbc:".length()));
    assertEquals("lexiflow_hook_test", origin.getPath().substring(1));
    int port = origin.getPort();
    String role = "lexiflow_test_" + UUID.randomUUID().toString().replace("-", "");
    cleanupJdbcUrl = leaseUrl;
    try (var admin = DriverManager.getConnection(leaseUrl);
        var statement = admin.createStatement()) {
      statement.execute("CREATE ROLE " + role + " LOGIN");
      createdRole = role;
      statement.execute("CREATE DATABASE lexiflow OWNER " + role);
      databaseCreated = true;
    }
    String url = "jdbc:postgresql://127.0.0.1:" + port + "/lexiflow?currentSchema=lexiflow_release";
    String code;
    try (var zip = new ZipFile(jar)) {
      var entry = zip.getEntry("META-INF/lexiflow-generation.json");
      assertTrue(entry != null);
      try (var stream = zip.getInputStream(entry)) {
        code =
            DatasetPackageCodec.object(
                    DatasetPackageCodec.parseJson(stream.readAllBytes()),
                    java.util.Set.of("codeVersion"))
                .get("codeVersion")
                .toString();
      }
    }
    Path csv = temporary.toRealPath().resolve("dictionary.csv");
    Files.writeString(csv, csv("alpha", "beta"));
    String generation = generation(code, sha(Files.readAllBytes(csv)));
    var local = GenerationIdentityReader.forInput(sha(Files.readAllBytes(csv)));
    assertEquals(code, local.codeVersion());
    var expectedIdentity =
        DatasetPackageCodec.object(
            DatasetPackageCodec.parseJson(generation),
            java.util.Set.of(
                "codeVersion",
                "sourceDigest",
                "generationVersion",
                "inputSha256",
                "config",
                "upstream"));
    assertEquals(local.sourceDigest(), expectedIdentity.get("sourceDigest"));
    assertEquals(local.generationVersion(), expectedIdentity.get("generationVersion"));
    io.lexiflow.lexicon.platform.importer.LexiconImportMain.validateDeploymentInput(
        csv, sha(Files.readAllBytes(csv)), local);
    Map<String, Object> missing =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertEquals("PASS", string(missing, "status"));
    assertTrue(flag(missing, "needsRebuild"));
    assertEquals("SCHEMA_MISSING", string(missing, "reason"));
    Map<String, Object> first =
        invoke(
            jar, url, role, "apply", request(csv, generation, string(missing, "fingerprint")), 0);
    assertTrue(flag(first, "rebuilt"));
    long version = number(first, "datasetVersion");
    Map<String, Object> complete =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertFalse(flag(complete, "needsRebuild"));
    assertEquals(
        "AUTHORIZATION_FAILED",
        string(
            invoke(jar, url, "postgres", "inspect", request(csv, generation, null), 3), "reason"));
    assertFalse(
        invoke(
                jar,
                url,
                role,
                "apply",
                request(csv, generation, string(complete, "fingerprint")),
                0)
            .get("rebuilt")
            .equals(Boolean.TRUE));

    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute(
          "UPDATE lexiflow_release.lexicon_entry SET gloss='corrupt' WHERE lemma='alpha'");
    }
    Map<String, Object> corrupt =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertTrue(flag(corrupt, "needsRebuild"));
    assertEquals(
        "FINGERPRINT_DRIFT",
        invoke(
                jar,
                url,
                role,
                "apply",
                request(csv, generation, string(complete, "fingerprint")),
                3)
            .get("reason"));
    Map<String, Object> repaired =
        invoke(
            jar, url, role, "apply", request(csv, generation, string(corrupt, "fingerprint")), 0);
    assertTrue(number(repaired, "datasetVersion") > version);

    Files.writeString(csv, csv("alpha", "gamma"));
    generation = generation(code, sha(Files.readAllBytes(csv)));
    Map<String, Object> changed =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertEquals("GENERATION_CHANGED", string(changed, "reason"));
    assertTrue(
        invoke(jar, url, role, "apply", request(csv, generation, string(changed, "fingerprint")), 0)
            .get("rebuilt")
            .equals(Boolean.TRUE));

    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("DELETE FROM lexiflow_release.lexicon_entry WHERE lemma='alpha'");
    }
    rebuildCurrent(jar, url, role, csv, generation);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("DROP INDEX lexiflow_release.lexicon_form_entry_idx");
    }
    rebuildCurrent(jar, url, role, csv, generation);

    Path invalidCsv = temporary.toRealPath().resolve("invalid.csv");
    Files.writeString(invalidCsv, "invalid CSV");
    String invalidGeneration = generation(code, sha(Files.readAllBytes(invalidCsv)));
    var beforeInvalid = invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertEquals(
        "FAIL",
        string(
            invoke(jar, url, role, "inspect", request(invalidCsv, invalidGeneration, null), 1),
            "status"));
    assertEquals(
        string(beforeInvalid, "fingerprint"),
        string(
            invoke(jar, url, role, "inspect", request(csv, generation, null), 0), "fingerprint"));

    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute(
          "UPDATE lexiflow_release.lexicon_dataset SET generation_version='"
              + "f".repeat(64)
              + "'");
    }
    var badMarker = invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertEquals("RECORD_INVALID", string(badMarker, "reason"));
    rebuildCurrent(jar, url, role, csv, generation);

    Path zip = temporary.toRealPath().resolve("dataset.zip");
    Path approval = temporary.toRealPath().resolve("approvals.json");
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT source_manifest->0->>'source_id',"
                    + "source_manifest->0->>'source_digest' FROM lexiflow_release.lexicon_dataset")) {
      assertTrue(rows.next());
      Files.writeString(
          approval,
          "{\"schemaVersion\":1,\"sources\":[{\"sourceId\":\""
              + rows.getString(1)
              + "\",\"sourceDigest\":\""
              + rows.getString(2)
              + "\",\"licenseId\":\"MIT\",\"redistributionApproved\":true,"
              + "\"evidenceUrl\":\"https://example.invalid/license\"}]}");
    }
    try (var connection = DriverManager.getConnection(url, role, "unused")) {
      PostgresDatasetExport.export(
          connection,
          zip,
          approval,
          Files.readAllBytes(Path.of(System.getProperty("lexiflow.postgres.schema.file"))));
    }
    assertFalse(sha(Files.readAllBytes(zip)).equals(sha(Files.readAllBytes(csv))));
    var manifest =
        DatasetPackageCodec.verifiedManifest(
            zip,
            sha(Files.readAllBytes(zip)),
            Files.readAllBytes(Path.of(System.getProperty("lexiflow.postgres.schema.file"))));
    var packageGeneration = GenerationIdentityReader.forInput(sha(Files.readAllBytes(csv)));
    assertEquals(packageGeneration.generationVersion(), manifest.generationVersion());
    assertEquals(
        DatasetPackageCodec.parseJson(packageGeneration.configJson()),
        DatasetPackageCodec.parseJson(manifest.generationConfigJson()));
    assertEquals(
        DatasetPackageCodec.parseJson(packageGeneration.inputsJson()),
        DatasetPackageCodec.parseJson(manifest.generationInputsJson()));
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute(
          "UPDATE lexiflow_release.lexicon_entry SET gloss='corrupt' WHERE lemma='alpha'");
    }
    var packageInspect =
        invoke(jar, url, role, "inspect", request(zip, generation, null, "package"), 0);
    assertTrue(flag(packageInspect, "needsRebuild"));
    assertTrue(
        flag(
            invoke(
                jar,
                url,
                role,
                "apply",
                request(zip, generation, string(packageInspect, "fingerprint"), "package"),
                0),
            "rebuilt"));
    var packageComplete =
        invoke(jar, url, role, "inspect", request(zip, generation, null, "package"), 0);
    assertFalse(flag(packageComplete, "needsRebuild"));
    assertFalse(
        flag(
            invoke(
                jar,
                url,
                role,
                "apply",
                request(zip, generation, string(packageComplete, "fingerprint"), "package"),
                0),
            "rebuilt"));

    Map<String, Object> beforeFailure =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    String adminUrl = "jdbc:postgresql://127.0.0.1:" + port + "/lexiflow";
    try (var admin = DriverManager.getConnection(adminUrl, "postgres", "unused");
        var statement = admin.createStatement()) {
      statement.execute(
          "CREATE FUNCTION public.lexiflow_test_abort() RETURNS event_trigger "
              + "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test ddl abort'; END $$");
      statement.execute(
          "CREATE EVENT TRIGGER lexiflow_test_abort ON ddl_command_start "
              + "WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION public.lexiflow_test_abort()");
    }
    try {
      Files.writeString(csv, csv("alpha", "epsilon"));
      String failedGeneration = generation(code, sha(Files.readAllBytes(csv)));
      var triggerPlan = invoke(jar, url, role, "inspect", request(csv, failedGeneration, null), 0);
      assertEquals(
          "FAIL",
          string(
              invoke(
                  jar,
                  url,
                  role,
                  "apply",
                  request(csv, failedGeneration, string(triggerPlan, "fingerprint")),
                  1),
              "status"));
    } finally {
      try (var admin = DriverManager.getConnection(adminUrl, "postgres", "unused");
          var statement = admin.createStatement()) {
        statement.execute("DROP EVENT TRIGGER lexiflow_test_abort");
        statement.execute("DROP FUNCTION public.lexiflow_test_abort()");
      }
    }
    Files.writeString(csv, csv("alpha", "gamma"));
    Map<String, Object> afterFailure =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertEquals(string(beforeFailure, "fingerprint"), string(afterFailure, "fingerprint"));
    assertFalse(flag(afterFailure, "needsRebuild"));

    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute(
          "ALTER TABLE lexiflow_release.lexicon_dataset DROP COLUMN generation_inputs");
    }
    rebuildCurrent(jar, url, role, csv, generation);

    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("CREATE TABLE lexiflow_release.legacy_extra (id integer)");
      statement.execute("CREATE INDEX legacy_extra_idx ON lexiflow_release.legacy_extra(id)");
    }
    rebuildCurrent(jar, url, role, csv, generation);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                    + "ON n.oid=c.relnamespace WHERE n.nspname='lexiflow_release' "
                    + "AND c.relname LIKE 'legacy_extra%'")) {
      assertTrue(rows.next());
      assertEquals(0, rows.getLong(1));
    }

    var cleanSchema = invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("CREATE TYPE lexiflow_release.unused_label AS ENUM ('unused')");
    }
    var extraType = invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertTrue(flag(extraType, "needsRebuild"));
    assertFalse(string(cleanSchema, "fingerprint").equals(string(extraType, "fingerprint")));
    rebuildCurrent(jar, url, role, csv, generation);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute(
          "CREATE FUNCTION lexiflow_release.unused_fn() RETURNS integer "
              + "LANGUAGE SQL IMMUTABLE AS $$ SELECT 7 $$");
    }
    rebuildCurrent(jar, url, role, csv, generation);

    Path desiredCsv = temporary.toRealPath().resolve("next.csv");
    Files.writeString(desiredCsv, csv("alpha", "delta"));
    String desiredGeneration = generation(code, sha(Files.readAllBytes(desiredCsv)));
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA observer");
      statement.execute("CREATE TABLE lexiflow_release.extra_parent (id integer PRIMARY KEY)");
      statement.execute("INSERT INTO lexiflow_release.extra_parent VALUES (1)");
      statement.execute(
          "CREATE TABLE observer.extra_child (id integer REFERENCES lexiflow_release.extra_parent(id))");
      statement.execute("INSERT INTO observer.extra_child VALUES (1)");
    }
    assertDependencyBlocked(
        jar,
        url,
        role,
        desiredCsv,
        desiredGeneration,
        generation,
        "SELECT count(*) FROM observer.extra_child",
        1);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("DROP TABLE observer.extra_child");
      statement.execute("DROP TABLE lexiflow_release.extra_parent");
      statement.execute("CREATE TYPE lexiflow_release.extra_label AS ENUM ('kept')");
      statement.execute("CREATE TABLE observer.extra_enum (value lexiflow_release.extra_label)");
      statement.execute("INSERT INTO observer.extra_enum VALUES ('kept')");
    }
    assertDependencyBlocked(
        jar,
        url,
        role,
        desiredCsv,
        desiredGeneration,
        generation,
        "SELECT count(*) FROM observer.extra_enum",
        1);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("DROP TABLE observer.extra_enum");
      statement.execute("DROP TYPE lexiflow_release.extra_label");
      statement.execute(
          "CREATE FUNCTION lexiflow_release.extra_answer() RETURNS integer "
              + "LANGUAGE SQL IMMUTABLE AS $$ SELECT 42 $$");
      statement.execute(
          "CREATE VIEW observer.extra_answer AS SELECT lexiflow_release.extra_answer() AS value");
    }
    assertDependencyBlocked(
        jar,
        url,
        role,
        desiredCsv,
        desiredGeneration,
        generation,
        "SELECT value FROM observer.extra_answer",
        42);
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("DROP VIEW observer.extra_answer");
      statement.execute("DROP FUNCTION lexiflow_release.extra_answer()");
      statement.execute(
          "CREATE VIEW observer.words AS SELECT lemma FROM lexiflow_release.lexicon_entry");
    }
    Map<String, Object> current =
        invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertDependencyBlocked(
        jar,
        url,
        role,
        desiredCsv,
        desiredGeneration,
        generation,
        "SELECT count(*) FROM observer.words",
        2);
    assertFalse(flag(current, "needsRebuild"));
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement();
        var rows = statement.executeQuery("SELECT count(*) FROM observer.words")) {
      assertTrue(rows.next());
      assertEquals(2, rows.getLong(1));
    }
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement()) {
      statement.execute("ALTER TABLE lexiflow_release.lexicon_entry ENABLE ROW LEVEL SECURITY");
      statement.execute("ALTER TABLE lexiflow_release.lexicon_entry FORCE ROW LEVEL SECURITY");
      statement.execute(
          "CREATE POLICY lexiflow_test_unreadable ON lexiflow_release.lexicon_entry "
              + "USING (current_setting('lexiflow.test_unreadable')::integer > 0)");
    }
    try {
      assertEquals(
          "DATABASE_UNAVAILABLE",
          string(invoke(jar, url, role, "inspect", request(csv, generation, null), 3), "reason"));
    } finally {
      try (var connection = DriverManager.getConnection(url, role, "unused");
          var statement = connection.createStatement()) {
        statement.execute("DROP POLICY lexiflow_test_unreadable ON lexiflow_release.lexicon_entry");
        statement.execute("ALTER TABLE lexiflow_release.lexicon_entry NO FORCE ROW LEVEL SECURITY");
        statement.execute("ALTER TABLE lexiflow_release.lexicon_entry DISABLE ROW LEVEL SECURITY");
      }
    }
  }

  private static String csv(String first, String second) {
    return "lemma,chinese_gloss,definition,aliases,inflections,frequency_zipf,frequency_source_id,frequency_license_id,frequency_ref,complex_evidence,dictionary_source_id,dictionary_license_id,dictionary_ref\n"
        + row(first)
        + row(second);
  }

  private void assertDependencyBlocked(
      String jar,
      String url,
      String role,
      Path desiredCsv,
      String desiredGeneration,
      String currentGeneration,
      String outsideQuery,
      long expected)
      throws Exception {
    assertEquals(
        "CROSS_SCHEMA_DEPENDENCY",
        string(
            invoke(jar, url, role, "inspect", request(desiredCsv, desiredGeneration, null), 3),
            "reason"));
    assertEquals(
        "CROSS_SCHEMA_DEPENDENCY",
        string(
            invoke(
                jar, url, role, "apply", request(desiredCsv, desiredGeneration, "0".repeat(64)), 3),
            "reason"));
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement();
        var rows = statement.executeQuery(outsideQuery)) {
      assertTrue(rows.next());
      assertEquals(expected, rows.getLong(1));
    }
    try (var connection = DriverManager.getConnection(url, role, "unused");
        var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT generation_version FROM lexiflow_release.lexicon_dataset")) {
      assertTrue(rows.next());
      var identity =
          DatasetPackageCodec.object(
              DatasetPackageCodec.parseJson(currentGeneration),
              java.util.Set.of(
                  "codeVersion",
                  "sourceDigest",
                  "generationVersion",
                  "inputSha256",
                  "config",
                  "upstream"));
      assertEquals(identity.get("generationVersion"), rows.getString(1).trim());
    }
  }

  private static String row(String lemma) {
    return lemma + ",释义,,, ,4,frequency,MIT,rank,,dictionary,MIT," + lemma + "\n";
  }

  private static String generation(String code, String input) throws Exception {
    String source =
        sha(
            ("{\"inputSha256\":\"" + input + "\",\"upstream\":{}}")
                .getBytes(StandardCharsets.UTF_8));
    String config = "{\"licenseId\":\"MIT\",\"sourceId\":\"ecdict-stardict\"}";
    String version =
        sha(
            ("{\"codeVersion\":\""
                    + code
                    + "\",\"config\":"
                    + config
                    + ",\"sourceDigest\":\""
                    + source
                    + "\"}")
                .getBytes(StandardCharsets.UTF_8));
    return "{\"codeVersion\":\""
        + code
        + "\",\"sourceDigest\":\""
        + source
        + "\",\"generationVersion\":\""
        + version
        + "\",\"inputSha256\":\""
        + input
        + "\",\"config\":"
        + config
        + ",\"upstream\":{}}";
  }

  private Path request(Path input, String generation, String fingerprint) throws Exception {
    return request(input, generation, fingerprint, "csv");
  }

  private Path request(Path input, String generation, String fingerprint, String type)
      throws Exception {
    Path request = Files.createTempFile(temporary.toRealPath(), "deployment-", ".json");
    String value =
        "{\"inputType\":\""
            + type
            + "\",\"inputPath\":\""
            + input
            + "\",\"inputSha256\":\""
            + sha(Files.readAllBytes(input))
            + "\",\"generation\":"
            + generation
            + (fingerprint == null ? "" : ",\"expectedFingerprint\":\"" + fingerprint + "\"")
            + "}";
    Files.writeString(request, value);
    return request;
  }

  private void rebuildCurrent(String jar, String url, String role, Path csv, String generation)
      throws Exception {
    var observed = invoke(jar, url, role, "inspect", request(csv, generation, null), 0);
    assertTrue(flag(observed, "needsRebuild"));
    assertTrue(
        flag(
            invoke(
                jar,
                url,
                role,
                "apply",
                request(csv, generation, string(observed, "fingerprint")),
                0),
            "rebuilt"));
  }

  private static Map<String, Object> invoke(
      String jar, String url, String role, String action, Path request, int expectedExit)
      throws Exception {
    var process =
        new ProcessBuilder(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-jar",
                jar,
                "--deployment-data",
                action,
                "--request",
                request.toString())
            .redirectErrorStream(true);
    process.environment().put("LEXIFLOW_DEPLOYMENT_JDBC_URL", url);
    process.environment().put("LEXIFLOW_DEPLOYMENT_DB_USER", role);
    process.environment().put("LEXIFLOW_DEPLOYMENT_DB_PASSWORD", "unused");
    var started = process.start();
    try {
      assertTrue(started.waitFor(45, TimeUnit.SECONDS), "deployment CLI timeout");
      String output = new String(started.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
      assertEquals(expectedExit, started.exitValue(), output);
      return DatasetPackageCodec.object(
          DatasetPackageCodec.parseJson(output),
          expectedExit == 0
              ? ("inspect".equals(action)
                  ? java.util.Set.of(
                      "status",
                      "database",
                      "schema",
                      "fingerprint",
                      "needsRebuild",
                      "reason",
                      "generationVersion",
                      "datasetVersion")
                  : java.util.Set.of("status", "rebuilt", "generationVersion", "datasetVersion"))
              : java.util.Set.of("status", "reason"));
    } finally {
      started.destroyForcibly();
    }
  }

  private static String string(Map<String, Object> value, String key) {
    return (String) value.get(key);
  }

  private static boolean flag(Map<String, Object> value, String key) {
    return (Boolean) value.get(key);
  }

  private static long number(Map<String, Object> value, String key) {
    return ((Number) value.get(key)).longValue();
  }

  private static String sha(byte[] bytes) throws Exception {
    return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
  }
}
