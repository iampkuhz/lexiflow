package io.lexiflow.integration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 验证真实合成资料生产链、输出归属拒绝及失败不发布成功清单。 */
class SyntheticReleaseDatasetProducerTest {
  @TempDir Path temp;

  @org.junit.jupiter.api.BeforeEach
  void canonicalizeOwnedTemporaryDirectory() throws Exception {
    temp = temp.toRealPath();
  }

  @Test
  void producesTwoVerifiedSyntheticGenerations() throws Exception {
    Path output = temp.resolve("release-fixtures");
    SyntheticReleaseDatasetProducer.produce(output);
    var result =
        new tools.jackson.databind.ObjectMapper().readTree(output.resolve("result.json").toFile());
    assertTrue(result.path("synthetic").asBoolean());
    assertFalse(result.path("formalReleaseEligible").asBoolean());
    assertTrue(Files.isRegularFile(output.resolve("dataset-a.zip")));
    assertTrue(Files.isRegularFile(output.resolve("dataset-b.zip")));
    assertEquals(2, result.path("datasets").size());
    assertFalse(
        result
            .path("datasets")
            .get(0)
            .path("sha256")
            .asString()
            .equals(result.path("datasets").get(1).path("sha256").asString()));
    assertFalse(
        result.path("datasets").get(0).path("datasetVersion").asLong()
            == result.path("datasets").get(1).path("datasetVersion").asLong());
    assertEquals(
        sha256(output.resolve("dataset-a.zip")),
        result.path("datasets").get(0).path("sha256").asString());
    assertEquals(
        sha256(output.resolve("dataset-b.zip")),
        result.path("datasets").get(1).path("sha256").asString());
  }

  @Test
  void requiresExplicitOutputAndRejectsRelativePath() {
    assertThrows(
        IllegalStateException.class, () -> SyntheticReleaseDatasetProducer.main(new String[0]));
    assertThrows(
        java.io.IOException.class,
        () -> SyntheticReleaseDatasetProducer.produce(Path.of("relative-output")));
  }

  @Test
  void refusesExistingOutputAndSymlinkParent() throws Exception {
    Path existing = Files.createDirectory(temp.resolve("existing"));
    assertThrows(
        java.io.IOException.class, () -> SyntheticReleaseDatasetProducer.produce(existing));
    Path link = temp.resolve("linked-parent");
    Files.createSymbolicLink(link, temp);
    assertThrows(
        java.io.IOException.class,
        () -> SyntheticReleaseDatasetProducer.produce(link.resolve("output")));
    assertFalse(Files.exists(existing.resolve("result.json")));
  }

  @Test
  void refusesRepositoryPrivateOutputPath() throws Exception {
    Path repository = Path.of(System.getProperty("lexiflow.repository.root")).toRealPath();
    Path output = repository.resolve("tmp/quality/forbidden-synthetic-fixtures");
    assertThrows(java.io.IOException.class, () -> SyntheticReleaseDatasetProducer.produce(output));
    assertFalse(Files.exists(output));
  }

  @Test
  void rejectsMissingIsolatedDatabaseBeforeOutputCreation() throws Exception {
    String previous = System.getProperty("lexiflow.postgres.test.jdbcUrl");
    System.clearProperty("lexiflow.postgres.test.jdbcUrl");
    Path output = temp.resolve("not-created");
    try {
      assertThrows(
          IllegalStateException.class, () -> SyntheticReleaseDatasetProducer.produce(output));
      assertFalse(Files.exists(output));
    } finally {
      if (previous != null) System.setProperty("lexiflow.postgres.test.jdbcUrl", previous);
    }
  }

  @Test
  void manifestIsWrittenOnlyAfterSuccessfulProduction() throws Exception {
    Path output = temp.resolve("export-failure");
    var attemptedExport = new java.util.concurrent.atomic.AtomicBoolean();
    assertThrows(
        java.io.IOException.class,
        () ->
            SyntheticReleaseDatasetProducer.produce(
                output,
                jdbcUrl -> {
                  attemptedExport.set(true);
                  try (var connection = java.sql.DriverManager.getConnection(jdbcUrl);
                      var statement = connection.createStatement()) {
                    statement.execute("DROP TABLE lexicon_hint_lookup CASCADE");
                  }
                }));
    assertTrue(attemptedExport.get());
    assertTrue(Files.isDirectory(output));
    assertFalse(Files.exists(output.resolve("result.json")));
  }

  private static String sha256(Path path) throws Exception {
    return java.util.HexFormat.of()
        .formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));
  }
}
