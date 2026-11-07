package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 只用合成小输入核对封闭格式和安全拒绝。 */
class DatasetPackageCodecTest {
  @TempDir java.nio.file.Path dir;

  @Test
  void rejectsDuplicateAndCoercedManifestFields() throws Exception {
    String base =
        "{\"schemaVersion\":4,\"schemaSha256\":\""
            + "a".repeat(64)
            + "\",\"approvalSha256\":\""
            + "b".repeat(64)
            + "\",\"datasetVersion\":1800000000000,\"preparationPolicy\":\""
            + HintPreparation.POLICY_ID
            + "\",\"generationVersion\":\""
            + fixtureHash("generation")
            + "\",\"generationCodeVersion\":\""
            + fixtureHash("code")
            + "\",\"generationSourceDigest\":\""
            + fixtureHash("source")
            + "\",\"generationInputs\":{\"inputSha256\":\""
            + fixtureHash("input")
            + "\",\"upstream\":{}}"
            + ",\"generationConfig\":{\"sourceId\":\"ecdict-stardict\",\"licenseId\":\"MIT\"}"
            + ",\"contentSha256\":\""
            + fixtureHash("content")
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
                base.replace("\"schemaVersion\":4", "\"schemaVersion\":4,\"extra\":true")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace("\"schemaVersion\":4", "\"schemaVersion\":1")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace("\"schemaVersion\":4", "\"schemaVersion\":3")
                    .getBytes(StandardCharsets.UTF_8)));
    assertThrows(
        IOException.class,
        () ->
            DatasetPackageCodec.parseManifest(
                base.replace("\"schemaVersion\":4,", "").getBytes(StandardCharsets.UTF_8)));
  }

  private static String fixtureHash(String content) throws Exception {
    return java.util.HexFormat.of()
        .formatHex(
            java.security.MessageDigest.getInstance("SHA-256")
                .digest(
                    ("dataset-package-codec-fixture:" + content).getBytes(StandardCharsets.UTF_8)));
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
  void checksAuthenticationBeforeReadAndRejectsMutatedBlocks() throws Exception {
    var file = dir.toRealPath().resolve("blocks.zip");
    byte[] bytes = new byte[64 * 1024 + 31];
    new java.util.Random(2579).nextBytes(bytes);
    Files.write(file, bytes);
    String hash = DatasetPackageCodec.sha256(file);
    try (var source = FileChannel.open(file, StandardOpenOption.READ);
        var writer = FileChannel.open(file, StandardOpenOption.WRITE)) {
      assertThrows(
          IOException.class,
          () -> new DatasetPackageCodec.AuthenticatedZip(source, "0".repeat(64)));
      source.position(0);
      try (var verified = new DatasetPackageCodec.AuthenticatedZip(source, hash)) {
        byte[] first = verified.readNBytes(64 * 1024);
        assertArrayEquals(Arrays.copyOf(bytes, first.length), first);
        writer.write(
            ByteBuffer.wrap(new byte[] {(byte) (bytes[bytes.length - 1] ^ 1)}), bytes.length - 1L);
        assertThrows(IOException.class, verified::read);
      }
    }
  }

  @Test
  void rejectsTrailingGrowthAndTruncationAfterAuthentication() throws Exception {
    var file = dir.toRealPath().resolve("changed-length.zip");
    byte[] bytes = {1, 2, 3};
    Files.write(file, bytes);
    String hash = DatasetPackageCodec.sha256(file);
    try (var writer = FileChannel.open(file, StandardOpenOption.WRITE);
        var source = FileChannel.open(file, StandardOpenOption.READ);
        var grown = new DatasetPackageCodec.AuthenticatedZip(source, hash)) {
      writer.write(ByteBuffer.wrap(new byte[] {4}), bytes.length);
      assertArrayEquals(bytes, grown.readNBytes(bytes.length));
      assertThrows(IOException.class, grown::read);
      writer.truncate(bytes.length);
    }
    try (var writer = FileChannel.open(file, StandardOpenOption.WRITE);
        var source = FileChannel.open(file, StandardOpenOption.READ);
        var truncated = new DatasetPackageCodec.AuthenticatedZip(source, hash)) {
      writer.truncate(2);
      assertThrows(IOException.class, truncated::read);
    }
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
