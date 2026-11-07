package io.lexiflow.api.deployment;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.platform.persistence.GenerationIdentityReader;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetExport;
import io.lexiflow.lexicon.platform.persistence.SqlitePersistence;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 部署请求预检失败仅输出固定结果，不应触及数据库。 */
@ExtendWith(OutputCaptureExtension.class)
class DeploymentDataCommandTest {
  @Test
  void inspectOfMissingDatabaseIsReadOnlyAndJdbcUrlMustMatchExactFile(@TempDir Path temp)
      throws Exception {
    Path database = temp.resolve("lexicon.db").toAbsolutePath();
    String jdbc = "jdbc:sqlite:" + database;
    var snapshot = DeploymentDataCommand.inspect(database, jdbc, new byte[] {1});
    assertTrue(snapshot.needsRebuild());
    assertFalse(Files.exists(database));
    assertEquals(jdbc, DeploymentDataCommand.deploymentUrl(jdbc, database));
    org.junit.jupiter.api.Assertions.assertThrows(
        IllegalArgumentException.class,
        () -> DeploymentDataCommand.deploymentUrl(jdbc + "?mode=ro", database));
    org.junit.jupiter.api.Assertions.assertThrows(
        IllegalArgumentException.class,
        () -> DeploymentDataCommand.deploymentUrl(jdbc, temp.resolve("x/../lexicon.db")));
  }

  @Test
  void firstInstallSameGenerationReuseAndChangedGenerationRebuildUseTheExactFile(
      @TempDir Path temp, CapturedOutput captured) throws Exception {
    Path root = temp.toRealPath();
    Path database = root.resolve("lexicon.db");
    String jdbc = "jdbc:sqlite:" + database;
    Path input = writeCsv(root, "alpha", "第一版");
    String inputSha = sha256(input);
    var generation = GenerationIdentityReader.forInput(inputSha);
    io.lexiflow.lexicon.platform.importer.LexiconImportMain.validateDeploymentInput(
        input, inputSha, generation);
    Path request = root.resolve("request.json");

    writeRequest(request, input, inputSha, generation, null);
    var requestJson = new ObjectMapper().readTree(Files.readAllBytes(request));
    DeploymentDataCommand.validateRequest(requestJson, false);
    DeploymentDataCommand.verifyGeneration(requestJson.get("generation"));
    assertEquals(0, invoke("inspect", request, database, jdbc), captured.getOut());
    JsonNode missing = lastJson(captured);
    assertTrue(missing.path("needsRebuild").asBoolean());
    assertEquals(database.toString(), missing.path("databaseFile").asString());
    assertFalse(Files.exists(database));

    String firstFingerprint = missing.path("fingerprint").asString();
    writeRequest(request, input, inputSha, generation, firstFingerprint);
    assertEquals(1, invokeWithSchema("apply", request, database, jdbc, new byte[0]));
    assertFalse(Files.exists(database), "failed first install must remove only its own new file");
    assertEquals(0, invoke("apply", request, database, jdbc));
    JsonNode installed = lastJson(captured);
    assertTrue(installed.path("rebuilt").asBoolean());
    long firstVersion = installed.path("datasetVersion").asLong();
    assertTrue(Files.isRegularFile(database));

    writeRequest(request, input, inputSha, generation, null);
    assertEquals(0, invoke("inspect", request, database, jdbc));
    JsonNode current = lastJson(captured);
    assertFalse(current.path("needsRebuild").asBoolean());
    writeRequest(request, input, inputSha, generation, current.path("fingerprint").asString());
    assertEquals(0, invoke("apply", request, database, jdbc));
    assertFalse(lastJson(captured).path("rebuilt").asBoolean());

    Path changedInput = writeCsv(root, "alpha", "第二版");
    String changedSha = sha256(changedInput);
    var changedGeneration = GenerationIdentityReader.forInput(changedSha);
    writeRequest(request, changedInput, changedSha, changedGeneration, null);
    assertEquals(0, invoke("inspect", request, database, jdbc));
    JsonNode changed = lastJson(captured);
    assertTrue(changed.path("needsRebuild").asBoolean());
    assertEquals("GENERATION_CHANGED", changed.path("reason").asString());
    String changedFingerprint = changed.path("fingerprint").asString();
    writeRequest(request, changedInput, changedSha, changedGeneration, changedFingerprint);
    assertEquals(1, invokeWithSchema("apply", request, database, jdbc, new byte[0]));
    assertTrue(Files.isRegularFile(database), "failed rebuild must preserve the existing target");
    writeRequest(request, input, inputSha, generation, null);
    assertEquals(0, invoke("inspect", request, database, jdbc));
    JsonNode afterRollback = lastJson(captured);
    assertFalse(afterRollback.path("needsRebuild").asBoolean());
    assertEquals(changedFingerprint, afterRollback.path("fingerprint").asString());

    writeRequest(request, changedInput, changedSha, changedGeneration, changedFingerprint);
    assertEquals(0, invoke("apply", request, database, jdbc));
    JsonNode rebuilt = lastJson(captured);
    assertTrue(rebuilt.path("rebuilt").asBoolean());
    assertTrue(rebuilt.path("datasetVersion").asLong() > firstVersion);
  }

  @Test
  void packageInspectAndApplyKeepPreflightAndRollbackContracts(
      @TempDir Path temp, CapturedOutput captured) throws Exception {
    Path root = temp.toRealPath();
    Path source = root.resolve("source.db");
    String sourceJdbc = "jdbc:sqlite:" + source;
    Path input = writeCsv(root, "reliable", "可靠");
    String inputSha = sha256(input);
    var generation = GenerationIdentityReader.forInput(inputSha);
    Path request = root.resolve("request.json");
    writeRequest(request, input, inputSha, generation, null);
    assertEquals(0, invoke("inspect", request, source, sourceJdbc));
    writeRequest(
        request, input, inputSha, generation, lastJson(captured).path("fingerprint").asString());
    assertEquals(0, invoke("apply", request, source, sourceJdbc));

    Path archive = root.resolve("dataset.zip");
    Path approvalFile = root.resolve("synthetic-approvals.json");
    var mapper = new ObjectMapper();
    try (var schema = getClass().getResourceAsStream("/META-INF/lexiflow-schema.sql");
        var connection = SqlitePersistence.connect(sourceJdbc)) {
      var approvals = new ArrayList<Map<String, Object>>();
      try (var statement = connection.createStatement();
          var rows = statement.executeQuery("SELECT source_manifest FROM lexicon_dataset")) {
        assertTrue(rows.next());
        for (var item : mapper.readTree(rows.getString(1))) {
          approvals.add(
              Map.of(
                  "sourceId", item.path("source_id").asString(),
                  "sourceDigest", item.path("source_digest").asString(),
                  "licenseId", item.path("license_id").asString(),
                  "redistributionApproved", true,
                  "evidenceUrl", "https://example.invalid/synthetic-only"));
        }
      }
      Files.writeString(
          approvalFile,
          mapper.writeValueAsString(Map.of("schemaVersion", 1, "sources", approvals)));
      SqliteDatasetExport.export(connection, archive, approvalFile, schema.readAllBytes());
    }

    Path target = root.resolve("target.db");
    String targetJdbc = "jdbc:sqlite:" + target;
    writePackageRequest(request, archive, sha256(archive), generation, null);
    assertEquals(0, invoke("inspect", request, target, targetJdbc));
    assertFalse(Files.exists(target));
    String fingerprint = lastJson(captured).path("fingerprint").asString();
    writePackageRequest(request, archive, sha256(archive), generation, "0".repeat(64));
    assertEquals(3, invoke("apply", request, target, targetJdbc));
    assertEquals("FINGERPRINT_DRIFT", lastJson(captured).path("reason").asString());
    assertFalse(Files.exists(target));

    writePackageRequest(request, archive, sha256(archive), generation, fingerprint);
    assertEquals(1, invokeWithSchema("apply", request, target, targetJdbc, new byte[0]));
    assertFalse(Files.exists(target));
    assertEquals(0, invoke("apply", request, target, targetJdbc));
    assertTrue(lastJson(captured).path("rebuilt").asBoolean());
    assertEquals(
        generation.generationVersion(), lastJson(captured).path("generationVersion").asString());
    try (var connection = SqlitePersistence.connect(targetJdbc);
        var statement = connection.createStatement();
        var rows = statement.executeQuery("SELECT COUNT(*) FROM lexicon_lookup")) {
      assertTrue(rows.next());
      assertEquals(1, rows.getInt(1));
    }
  }

  private static void writePackageRequest(
      Path request,
      Path packagePath,
      String packageSha,
      io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata.GenerationIdentity
          generation,
      String fingerprint)
      throws Exception {
    writeRequest(request, packagePath, packageSha, generation, fingerprint, "package");
  }

  private static int invokeWithSchema(
      String action, Path request, Path database, String jdbc, byte[] sql) {
    return DeploymentDataCommand.run(
        new String[] {
          "--deployment-data",
          action,
          "--request",
          request.toString(),
          "--expected-file",
          database.toString()
        },
        jdbc,
        sql);
  }

  private static int invoke(String action, Path request, Path database, String jdbc) {
    return DeploymentDataCommand.run(
        new String[] {
          "--deployment-data",
          action,
          "--request",
          request.toString(),
          "--expected-file",
          database.toString()
        },
        jdbc);
  }

  private static Path writeCsv(Path root, String lemma, String gloss) throws Exception {
    Path file = root.resolve("dataset-" + gloss + ".csv");
    Files.writeString(
        file,
        "lemma,chinese_gloss,definition,aliases,inflections,frequency_zipf,frequency_source_id,frequency_license_id,frequency_ref,complex_evidence,dictionary_source_id,dictionary_license_id,dictionary_ref\n"
            + lemma
            + ",\""
            + gloss
            + "\",,,,6.10,synthetic,CC0,freq-"
            + lemma
            + ",,synthetic,CC0,dict-"
            + lemma
            + "\n");
    return file;
  }

  private static void writeRequest(
      Path request,
      Path input,
      String inputSha,
      io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata.GenerationIdentity
          generation,
      String fingerprint)
      throws Exception {
    writeRequest(request, input, inputSha, generation, fingerprint, "csv");
  }

  private static void writeRequest(
      Path request,
      Path input,
      String inputSha,
      io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata.GenerationIdentity
          generation,
      String fingerprint,
      String inputType)
      throws Exception {
    String generationJson =
        "{\"codeVersion\":\""
            + generation.codeVersion()
            + "\",\"sourceDigest\":\""
            + generation.sourceDigest()
            + "\",\"generationVersion\":\""
            + generation.generationVersion()
            + "\",\"inputSha256\":\""
            + generation.inputSha256()
            + "\",\"config\":{\"sourceId\":\"ecdict-stardict\",\"licenseId\":\"MIT\"},\"upstream\":{}}";
    String value =
        "{\"inputType\":\""
            + inputType
            + "\",\"inputPath\":\""
            + input
            + "\",\"inputSha256\":\""
            + inputSha
            + "\",\"generation\":"
            + generationJson
            + (fingerprint == null ? "" : ",\"expectedFingerprint\":\"" + fingerprint + "\"")
            + "}";
    Files.writeString(request, value);
  }

  private static JsonNode lastJson(CapturedOutput captured) throws Exception {
    var lines = captured.getOut().lines().toList();
    return new ObjectMapper().readTree(lines.get(lines.size() - 1));
  }

  private static String sha256(Path path) throws Exception {
    return HexFormat.of()
        .formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));
  }

  @Test
  void rejectsArtifactGenerationMismatchBeforeDatabaseConnection(CapturedOutput captured)
      throws Exception {
    var input = Files.createTempFile("deployment-input-", ".csv");
    var request = Files.createTempFile("deployment-request-", ".json");
    try {
      Files.writeString(input, "not a dataset");
      var sha =
          java.util.HexFormat.of()
              .formatHex(
                  java.security.MessageDigest.getInstance("SHA-256")
                      .digest(Files.readAllBytes(input)));
      Files.writeString(
          request,
          """
          {"inputType":"csv","inputPath":"%s","inputSha256":"%s","generation":{
          "codeVersion":"%s","sourceDigest":"%s","generationVersion":"%s","inputSha256":"%s",
          "config":{"sourceId":"ecdict-stardict","licenseId":"MIT"},"upstream":{}}}
          """
              .formatted(input, sha, "0".repeat(64), "1".repeat(64), "2".repeat(64), sha));
      assertEquals(
          1,
          DeploymentDataCommand.run(
              new String[] {
                "--deployment-data",
                "inspect",
                "--request",
                request.toString(),
                "--expected-file",
                "/tmp/not-created.db"
              }));
      assertEquals(
          "{\"status\":\"FAIL\",\"reason\":\"REQUEST_OR_DATA_INVALID\"}\n", captured.getOut());
    } finally {
      Files.deleteIfExists(request);
      Files.deleteIfExists(input);
    }
  }
}
