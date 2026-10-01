package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.UUID;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 隔离 PostgreSQL 中的真实三表发布快照，含并发源更新。 */
@Tag("postgres")
class PostgresDatasetExportIntegrationTest {
  @TempDir Path temporary;

  @Test
  void exportsDeterministicallyAndKeepsOneSnapshotAcrossConcurrentUpdate() throws Exception {
    try (Fixture source = Fixture.create(true)) {
      Path dir = temporary.toRealPath();
      Path approval = approval(dir);
      byte[] schema = schema();
      Path first = dir.resolve("first.zip");
      try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
        var result =
            PostgresDatasetExport.export(
                connection,
                first,
                approval,
                schema,
                true,
                () -> {
                  try (var update = DriverManager.getConnection(source.jdbcUrl());
                      var statement = update.createStatement()) {
                    statement.executeUpdate(
                        "UPDATE lexicon_prepared_entry SET source_gloss='newer' WHERE lemma='reliable'");
                  } catch (SQLException exception) {
                    throw new IllegalStateException(exception);
                  }
                });
        assertEquals(1, result.datasetVersion());
        DatasetPackageCodec.verify(first, result.sha256(), schema);
        try (var checked = DatasetPackageCodec.verifiedPackage(first, result.sha256(), schema)) {
          assertTrue(Files.readString(checked.file("prepared.ndjson")).contains("old-gloss"));
          assertTrue(!Files.readString(checked.file("prepared.ndjson")).contains("newer"));
        }
      }
      Path second = dir.resolve("second.zip");
      try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
        var result = PostgresDatasetExport.export(connection, second, approval, schema);
        assertTrue(!result.sha256().equals(DatasetPackageCodec.sha256(first)));
      }
    }
  }

  @Test
  void identicalSnapshotsProduceIdenticalZipAndTamperedApprovalIsRejected() throws Exception {
    try (Fixture source = Fixture.create(true)) {
      Path dir = temporary.toRealPath();
      Path approval = approval(dir);
      byte[] sql = schema();
      Path first = dir.resolve("first.zip");
      Path second = dir.resolve("second.zip");
      try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
        PostgresDatasetExport.export(connection, first, approval, sql);
      }
      try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
        PostgresDatasetExport.export(connection, second, approval, sql);
      }
      assertEquals(DatasetPackageCodec.sha256(first), DatasetPackageCodec.sha256(second));
      try (var checked =
          DatasetPackageCodec.verifiedPackage(first, DatasetPackageCodec.sha256(first), sql)) {
        Files.writeString(
            checked.file("approvals.json"),
            Files.readString(checked.file("approvals.json"))
                .replace("example.invalid", "tampered.invalid"));
        Path tampered = dir.resolve("tampered.zip");
        DatasetPackageCodec.write(
            tampered,
            java.util.List.of(
                checked.file("manifest.json"),
                checked.file("approvals.json"),
                checked.file("dataset.ndjson"),
                checked.file("prepared.ndjson"),
                checked.file("lookup.ndjson")));
        assertThrows(
            IOException.class,
            () -> DatasetPackageCodec.verify(tampered, DatasetPackageCodec.sha256(tampered), sql));
      }
    }
  }

  @Test
  void rejectsApprovalMismatchAndIncompleteProjection() throws Exception {
    try (Fixture source = Fixture.create(true)) {
      Path dir = temporary.toRealPath();
      byte[] schema = schema();
      Path badApproval = dir.resolve("bad-approval.json");
      Files.writeString(badApproval, Files.readString(approval(dir)).replace("MIT", "Apache-2.0"));
      try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetExport.export(
                    connection, dir.resolve("bad.zip"), badApproval, schema));
      }
      try (var connection = DriverManager.getConnection(source.jdbcUrl());
          var statement = connection.createStatement()) {
        statement.executeUpdate(
            "UPDATE lexicon_hint_lookup SET canonical_lemma='wrong' WHERE normalized_form='reliable'");
      }
      try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetExport.export(
                    connection, dir.resolve("broken.zip"), approval(dir), schema));
      }
    }
  }

  static byte[] schema() throws IOException {
    return Files.readAllBytes(Path.of(System.getProperty("lexiflow.postgres.schema.file")));
  }

  static Path approval(Path dir) throws IOException {
    Path path = dir.resolve("approvals.json");
    Files.writeString(
        path,
        "{\"schemaVersion\":1,\"sources\":[{\"sourceId\":\"fixture\","
            + "\"sourceDigest\":\""
            + "a".repeat(64)
            + "\",\"licenseId\":\"MIT\","
            + "\"redistributionApproved\":true,\"evidenceUrl\":\"https://example.invalid/license\"}]}",
        StandardCharsets.UTF_8);
    return path;
  }

  static final class Fixture implements AutoCloseable {
    private final String admin;
    private final String schemaName;
    private final String jdbcUrl;

    private Fixture(String admin, String schemaName, String jdbcUrl) {
      this.admin = admin;
      this.schemaName = schemaName;
      this.jdbcUrl = jdbcUrl;
    }

    String jdbcUrl() {
      return jdbcUrl;
    }

    String schemaName() {
      return schemaName;
    }

    String database() throws SQLException {
      try (var connection = DriverManager.getConnection(jdbcUrl);
          var statement = connection.createStatement();
          var result = statement.executeQuery("SELECT current_database()")) {
        result.next();
        return result.getString(1);
      }
    }

    static Fixture create(boolean published) throws Exception {
      String admin = System.getProperty("lexiflow.postgres.test.jdbcUrl", "");
      if (admin.isBlank()) throw new IllegalStateException("isolated PostgreSQL required");
      String name = "lexiflow_dataset_" + UUID.randomUUID().toString().replace("-", "");
      try (var connection = DriverManager.getConnection(admin);
          var statement = connection.createStatement()) {
        statement.execute("CREATE SCHEMA \"" + name + "\"");
      }
      String url = admin + (admin.contains("?") ? "&" : "?") + "currentSchema=" + name;
      var fixture = new Fixture(admin, name, url);
      if (published) {
        PostgresSchemaInitializer.initialize(
            url, Path.of(System.getProperty("lexiflow.postgres.schema.file")));
        fixture.publish();
      }
      return fixture;
    }

    private void publish() throws SQLException {
      try (var connection = DriverManager.getConnection(jdbcUrl);
          var statement = connection.createStatement()) {
        statement.executeUpdate(
            "INSERT INTO lexicon_prepared_entry (lexicon_entry_id,language_tag,lemma,entry_kind,"
                + "source_gloss,source_gloss_ref,source_dictionary_id,source_frequency_id,source_frequency_ref,"
                + "source_complex_tags,source_oxford_basic,prepared_gloss,frequency_evidence,decisive_rule,"
                + "matched_rules,prepared_priority,frequency_zipf,complex_list_count) VALUES ("
                + "'11111111-1111-1111-1111-111111111111','en','reliable','word','old-gloss','ref','fixture',"
                + "'fixture','ref',ARRAY['advanced'],false,'safe','KNOWN','fixture',ARRAY['fixture'],100,3.25,1)");
        statement.executeUpdate(
            "INSERT INTO lexicon_hint_lookup (language_tag,normalized_form,lexicon_entry_id,"
                + "form_kind,canonical_lemma,entry_kind,final_action,final_decision_reason,final_gloss,"
                + "final_priority,final_sense_id,final_frequency_zipf,final_complex_list_count,cache_priority) VALUES ("
                + "'en','reliable','11111111-1111-1111-1111-111111111111','lemma','reliable','word','HINT',"
                + "'fixture','safe',100,'22222222-2222-2222-2222-222222222222',3.25,1,100)");
        try (var dataset =
            connection.prepareStatement(
                "INSERT INTO lexicon_dataset (dataset_id,lexicon_version,"
                    + "source_manifest,source_row_count,entry_count,lookup_count,preparation_policy,imported_at) "
                    + "VALUES (1,1,?::jsonb,1,1,1,?,'1970-01-01T00:00:00Z')")) {
          dataset.setString(
              1,
              "[{\"source_id\":\"fixture\",\"license_id\":\"MIT\",\"source_digest\":\""
                  + "a".repeat(64)
                  + "\",\"acquired_at\":\"1970-01-01T00:00:00Z\"}]");
          dataset.setString(2, HintPreparation.POLICY_ID);
          dataset.executeUpdate();
        }
      }
    }

    @Override
    public void close() throws SQLException {
      try (var connection = DriverManager.getConnection(admin);
          var statement = connection.createStatement()) {
        statement.execute("DROP SCHEMA \"" + schemaName + "\" CASCADE");
      }
    }
  }
}
