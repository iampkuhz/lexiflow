package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipFile;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 通过真实 API JAR 验证部署 SQLite 文件预检、首装、复用及完整重导。 */
@Tag("sqlite")
class DeploymentDataJarIntegrationTest {
  @TempDir Path temporary;

  @Test
  void realJarInspectsMissingFileAppliesAndReusesThenPublishesChangedInput() throws Exception {
    String jar = System.getProperty("lexiflow.release.test.bootJar", "");
    assertTrue(!jar.isBlank(), "bootJar path must be supplied");
    Path dir = temporary.toRealPath();
    String code = generationCode(jar);
    Path csv = dir.resolve("dictionary.csv");
    Files.writeString(csv, csv("alpha", "beta"));
    String generation = generation(code, sha(Files.readAllBytes(csv)));
    Path database = dir.resolve("runtime.db");
    String url = SqliteTestSupport.url(database);

    Map<String, Object> missing =
        invoke(jar, url, "inspect", request(csv, generation, null), database, 0);
    assertEquals("PASS", missing.get("status"));
    assertTrue((Boolean) missing.get("needsRebuild"));
    assertEquals("FILE_MISSING", missing.get("reason"));
    assertFalse(Files.exists(database), "inspect must not create database file");

    Map<String, Object> installed =
        invoke(
            jar,
            url,
            "apply",
            request(csv, generation, (String) missing.get("fingerprint")),
            database,
            0);
    assertEquals("PASS", installed.get("status"));
    assertEquals(Boolean.TRUE, installed.get("rebuilt"));
    long version = ((Number) installed.get("datasetVersion")).longValue();
    assertTrue(version > 0);

    Map<String, Object> complete =
        invoke(jar, url, "inspect", request(csv, generation, null), database, 0);
    assertFalse((Boolean) complete.get("needsRebuild"));
    Map<String, Object> reused =
        invoke(
            jar,
            url,
            "apply",
            request(csv, generation, (String) complete.get("fingerprint")),
            database,
            0);
    assertEquals(Boolean.FALSE, reused.get("rebuilt"));
    assertEquals(version, ((Number) reused.get("datasetVersion")).longValue());

    Files.writeString(csv, csv("alpha", "gamma"));
    String changedGeneration = generation(code, sha(Files.readAllBytes(csv)));
    Map<String, Object> changed =
        invoke(jar, url, "inspect", request(csv, changedGeneration, null), database, 0);
    assertEquals("GENERATION_CHANGED", changed.get("reason"));
    Map<String, Object> replaced =
        invoke(
            jar,
            url,
            "apply",
            request(csv, changedGeneration, (String) changed.get("fingerprint")),
            database,
            0);
    assertEquals(Boolean.TRUE, replaced.get("rebuilt"));
    assertTrue(((Number) replaced.get("datasetVersion")).longValue() > version);

    Path wrongFile = dir.resolve("wrong.db");
    Map<String, Object> mismatch =
        invoke(jar, url, "inspect", request(csv, changedGeneration, null), wrongFile, 1);
    assertEquals("FAIL", mismatch.get("status"));
    assertFalse(Files.exists(wrongFile));

    try (var connection = SqlitePersistence.connect(url)) {
      var verified = SqliteDatasetVerifier.verify(connection, SqliteTestSupport.schema());
      assertEquals(2, verified.entryCount());
      assertEquals(2, verified.formCount());
    }
  }

  private static Map<String, Object> invoke(
      String jar, String jdbcUrl, String action, Path request, Path expectedFile, int expectedExit)
      throws Exception {
    var process =
        new ProcessBuilder(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-jar",
                jar,
                "--deployment-data",
                action,
                "--request",
                request.toString(),
                "--expected-file",
                expectedFile.toAbsolutePath().normalize().toString())
            .redirectErrorStream(true);
    process.environment().put("LEXIFLOW_DEPLOYMENT_JDBC_URL", jdbcUrl);
    var started = process.start();
    try {
      assertTrue(
          started.waitFor(45, java.util.concurrent.TimeUnit.SECONDS), "deployment CLI timeout");
      String rawOutput =
          new String(started.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
      assertEquals(expectedExit, started.exitValue(), rawOutput);
      String output =
          rawOutput
              .lines()
              .filter(line -> line.startsWith("{"))
              .reduce((first, last) -> last)
              .orElseThrow(
                  () -> new AssertionError("deployment JSON output missing: " + rawOutput));
      Set<String> fields =
          output.contains("\"status\":\"FAIL\"")
              ? Set.of("status", "reason")
              : "inspect".equals(action)
                  ? Set.of(
                      "status",
                      "databaseFile",
                      "fingerprint",
                      "needsRebuild",
                      "reason",
                      "generationVersion",
                      "datasetVersion")
                  : Set.of("status", "rebuilt", "generationVersion", "datasetVersion");
      return DatasetPackageCodec.object(DatasetPackageCodec.parseJson(output), fields);
    } finally {
      started.destroyForcibly();
    }
  }

  private Path request(Path input, String generation, String fingerprint) throws Exception {
    Path request = Files.createTempFile(temporary.toRealPath(), "deployment-", ".json");
    String json =
        "{\"inputType\":\"csv\",\"inputPath\":\""
            + input
            + "\",\"inputSha256\":\""
            + sha(Files.readAllBytes(input))
            + "\",\"generation\":"
            + generation
            + (fingerprint == null ? "" : ",\"expectedFingerprint\":\"" + fingerprint + "\"")
            + "}";
    Files.writeString(request, json, StandardCharsets.UTF_8);
    return request;
  }

  private static String generationCode(String jar) throws Exception {
    try (var zip = new ZipFile(jar)) {
      var entry = zip.getEntry("META-INF/lexiflow-generation.json");
      assertTrue(entry != null);
      try (var stream = zip.getInputStream(entry)) {
        return DatasetPackageCodec.object(
                DatasetPackageCodec.parseJson(stream.readAllBytes()), Set.of("codeVersion"))
            .get("codeVersion")
            .toString();
      }
    }
  }

  private static String csv(String first, String second) {
    return "lemma,chinese_gloss,definition,aliases,inflections,frequency_zipf,frequency_source_id,frequency_license_id,frequency_ref,complex_evidence,dictionary_source_id,dictionary_license_id,dictionary_ref\n"
        + row(first)
        + row(second);
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

  private static String sha(byte[] bytes) throws Exception {
    return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
  }
}
