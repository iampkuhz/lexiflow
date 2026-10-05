package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 只用合成小输入核对封闭格式和安全拒绝。 */
class DatasetPackageCodecTest {
  @TempDir java.nio.file.Path dir;

  @Test
  void rejectsDuplicateAndCoercedManifestFields() throws Exception {
    String base =
        "{\"schemaVersion\":2,\"schemaSha256\":\""
            + "a".repeat(64)
            + "\",\"approvalSha256\":\""
            + "b".repeat(64)
            + "\",\"datasetVersion\":1800000000000,\"preparationPolicy\":\""
            + HintPreparation.POLICY_ID
            + "\",\"files\":["
            + file("dataset.ndjson", 1)
            + ","
            + file("entries.ndjson", 1)
            + ","
            + file("forms.ndjson", 1)
            + "]}";
    assertEquals(
        1_800_000_000_000L,
        DatasetPackageCodec.parseManifest(base.getBytes(StandardCharsets.UTF_8)).datasetVersion());
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace(
                        "\"datasetVersion\":1800000000000", "\"datasetVersion\":\"1800000000000\"")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace(
                        "\"datasetVersion\":1800000000000",
                        "\"datasetVersion\":1800000000000,\"datasetVersion\":1800000000000")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace("\"schemaVersion\":2", "\"schemaVersion\":2,\"extra\":true")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace("\"schemaVersion\":2", "\"schemaVersion\":1")
                    .getBytes(StandardCharsets.UTF_8)));
  }

  @Test
  void rejectsUnsafeApprovalAndRowShape() {
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseApprovals(
                "{\"schemaVersion\":1,\"sources\":[{\"sourceId\":\"x\",\"sourceDigest\":\""
                    .concat("a".repeat(64))
                    .concat(
                        "\",\"licenseId\":\"MIT\",\"redistributionApproved\":true,\"evidenceUrl\":\"https://user:pass@example.invalid/\"}]}")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.validateRow(Map.of("dataset_id", 1), DatasetPackageTables.DATASET));
  }

  @Test
  void sameOutputIsReusedButDifferentOutputIsNeverReplaced() throws Exception {
    var parts = new java.util.ArrayList<java.nio.file.Path>();
    for (int i = 0; i < 5; i++) {
      var file = dir.toRealPath().resolve("part" + i);
      Files.writeString(file, "synthetic-" + i);
      parts.add(file);
    }
    var output = dir.toRealPath().resolve("package.zip");
    DatasetPackageCodec.write(output, parts);
    String digest = DatasetPackageCodec.sha256(output);
    DatasetPackageCodec.write(output, parts);
    assertEquals(digest, DatasetPackageCodec.sha256(output));
    Files.writeString(parts.get(3), "different");
    assertThrows(IOException.class, () -> DatasetPackageCodec.write(output, parts));
    assertEquals(digest, DatasetPackageCodec.sha256(output));
  }

  @Test
  void zipBytesAndLocalDosTimeDoNotDependOnHostTimezone() throws Exception {
    var parts = new java.util.ArrayList<java.nio.file.Path>();
    Path canonical = dir.toRealPath();
    for (int i = 0; i < 5; i++) {
      Path part = canonical.resolve("zone-part" + i);
      Files.writeString(part, "synthetic-" + i);
      parts.add(part);
    }
    var original = java.util.TimeZone.getDefault();
    try {
      java.util.TimeZone.setDefault(java.util.TimeZone.getTimeZone("UTC"));
      Path utc = canonical.resolve("utc.zip");
      DatasetPackageCodec.write(utc, parts);
      java.util.TimeZone.setDefault(java.util.TimeZone.getTimeZone("Asia/Shanghai"));
      Path shanghai = canonical.resolve("shanghai.zip");
      DatasetPackageCodec.write(shanghai, parts);
      assertEquals(DatasetPackageCodec.sha256(utc), DatasetPackageCodec.sha256(shanghai));
      try (var zip = new java.util.zip.ZipFile(shanghai.toFile())) {
        assertEquals(
            java.time.LocalDateTime.of(2000, 1, 1, 0, 0),
            zip.entries().nextElement().getTimeLocal());
      }
    } finally {
      java.util.TimeZone.setDefault(original);
    }
  }

  private static String file(String name, int rows) {
    return "{\"name\":\""
        + name
        + "\",\"bytes\":1,\"sha256\":\""
        + "c".repeat(64)
        + "\",\"rows\":"
        + rows
        + "}";
  }
}
