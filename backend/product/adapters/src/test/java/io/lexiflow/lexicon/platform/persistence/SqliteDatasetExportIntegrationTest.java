package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 真实 SQLite 快照导出与数据摘要合同。 */
@Tag("sqlite")
class SqliteDatasetExportIntegrationTest {
  @TempDir Path temporary;

  @Test
  void exportsCanonicalLookupAndPhysicalFormsDeterministically() throws Exception {
    Path source = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(source);
    long version =
        SqliteTestSupport.publish(
            source,
            SqliteTestSupport.row("reliable", "可靠的", List.of("dependable"), List.of("reliably")),
            SqliteTestSupport.row("reliably", "可靠地", List.of(), List.of()));
    Path dir = temporary.toRealPath();
    Path approval = SqliteTestSupport.approval(dir);
    byte[] schema = SqliteTestSupport.schema();
    Path first = dir.resolve("first.zip");
    Path second = dir.resolve("second.zip");
    SqliteDatasetExport.Result result;
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(source))) {
      result = SqliteDatasetExport.export(connection, first, approval, schema);
      assertEquals(version, result.datasetVersion());
      assertEquals(
          2, SqliteDatasetVerifier.verify(connection, SqliteTestSupport.schema()).entryCount());
      assertEquals(
          4, SqliteDatasetVerifier.verify(connection, SqliteTestSupport.schema()).formCount());
      assertEquals(2, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_form"));
      assertEquals(4, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_lookup"));
      try (var verified = DatasetPackageCodec.verifiedPackage(first, result.sha256(), schema)) {
        assertEquals(version, verified.manifest().datasetVersion());
      }
      SqliteDatasetExport.export(connection, second, approval, schema);
    }
    assertEquals(DatasetPackageCodec.sha256(first), DatasetPackageCodec.sha256(second));
    assertEquals(2, Files.readAllLines(unzip(first, "entries.ndjson")).size());
    assertEquals(2, Files.readAllLines(unzip(first, "forms.ndjson")).size());
  }

  @Test
  void rejectsModifiedPackageAndApprovalEvidence() throws Exception {
    Path source = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(source);
    SqliteTestSupport.publish(
        source, SqliteTestSupport.row("reliable", "可靠的", List.of(), List.of()));
    Path dir = temporary.toRealPath();
    Path approval = SqliteTestSupport.approval(dir);
    Path out = dir.resolve("snapshot.zip");
    String digest;
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(source))) {
      digest =
          SqliteDatasetExport.export(connection, out, approval, SqliteTestSupport.schema())
              .sha256();
    }
    byte[] original = Files.readAllBytes(out);
    original[original.length / 2] ^= 1;
    Path corrupt = dir.resolve("corrupt.zip");
    Files.write(corrupt, original);
    assertThrows(
        Exception.class,
        () -> DatasetPackageCodec.verify(corrupt, digest, SqliteTestSupport.schema()));
    Files.writeString(approval, "{}\n");
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(source))) {
      assertThrows(
          Exception.class,
          () ->
              SqliteDatasetExport.export(
                  connection, dir.resolve("bad.zip"), approval, SqliteTestSupport.schema()));
    }
    assertNotEquals(digest, DatasetPackageCodec.sha256(corrupt));
  }

  private Path unzip(Path zipPath, String member) throws Exception {
    try (var zip = new java.util.zip.ZipFile(zipPath.toFile())) {
      var entry = zip.getEntry(member);
      assertTrue(entry != null);
      Path path = temporary.toRealPath().resolve("extract-" + member);
      try (var input = zip.getInputStream(entry)) {
        Files.copy(input, path);
      }
      return path;
    }
  }
}
