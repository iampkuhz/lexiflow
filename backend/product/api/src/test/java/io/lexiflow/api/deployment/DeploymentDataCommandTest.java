package io.lexiflow.api.deployment;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.platform.persistence.GenerationIdentityReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
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
        "{\"inputType\":\"csv\",\"inputPath\":\""
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
