package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.zaxxer.hikari.HikariDataSource;
import io.lexiflow.lexicon.application.LexiconImportMetadata;
import io.lexiflow.lexicon.application.LexiconImportRequest;
import io.lexiflow.lexicon.application.LexiconImportRow;
import io.lexiflow.lexicon.application.SourceReference;
import io.lexiflow.lexicon.domain.LexiconPriority;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 在显式隔离 schema 上验证迁移、索引、持久化发布和查询路径。 */
@Tag("postgres")
class PostgresLexiconRepositoryIntegrationTest {
  private static final String JDBC_URL_PROPERTY = "lexiflow.postgres.test.jdbcUrl";
  private static final String MIGRATIONS_PROPERTY = "lexiflow.postgres.migrations.dir";
  private static String adminJdbcUrl;
  private static String isolatedJdbcUrl;
  private static String schema;

  @BeforeAll
  static void createIsolatedSchemaAndApplyMigrations() throws Exception {
    adminJdbcUrl = requiredJdbcUrl();
    schema = createSchema();
    isolatedJdbcUrl = withSchema(adminJdbcUrl, schema);
    assertEquals(
        List.of(1, 2, 3, 4, 5),
        PostgresSchemaMigrator.apply(
            isolatedJdbcUrl, Path.of(requiredMigrationsDirectory()), null));
  }

  @AfterAll
  static void cleanIsolatedSchema() throws Exception {
    if (adminJdbcUrl != null && schema != null) {
      dropSchema(schema);
    }
  }

  @Test
  void locksMigrationHistoryAndCreatesTheDeclaredQueryIndex() throws Exception {
    assertEquals(
        List.of(),
        PostgresSchemaMigrator.apply(
            isolatedJdbcUrl, Path.of(requiredMigrationsDirectory()), null));
    assertEquals(
        "lexicon_entry_lookup_idx",
        scalar(isolatedJdbcUrl, "SELECT to_regclass('lexicon_entry_lookup_idx')"));
    assertEquals("5", scalar(isolatedJdbcUrl, "SELECT max(version)::text FROM schema_migration"));
  }

  @Test
  void rollsBackAFailingMigrationAndRequiresForwardCompensation(@TempDir Path temporaryDirectory)
      throws Exception {
    var migrationDirectory = copyMigrations(temporaryDirectory);
    var testSchema = createSchema();
    var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
    try {
      assertEquals(
          List.of(1, 2, 3, 4, 5), PostgresSchemaMigrator.apply(jdbcUrl, migrationDirectory, null));
      Files.writeString(
          migrationDirectory.resolve("V006__failing_change.sql"),
          "CREATE TABLE atomic_probe (id INTEGER PRIMARY KEY);\nSELECT 1 / 0;\n");
      assertThrows(
          SQLException.class,
          () -> PostgresSchemaMigrator.apply(jdbcUrl, migrationDirectory, null));
      assertEquals("", scalar(jdbcUrl, "SELECT COALESCE(to_regclass('atomic_probe')::text, '')"));
      assertEquals("5", scalar(jdbcUrl, "SELECT max(version)::text FROM schema_migration"));
    } finally {
      dropSchema(testSchema);
    }
  }

  @Test
  void rejectsAnAppliedMigrationWhoseChecksumChanges(@TempDir Path temporaryDirectory)
      throws Exception {
    var migrationDirectory = copyMigrations(temporaryDirectory);
    var testSchema = createSchema();
    var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
    try {
      assertEquals(List.of(1), PostgresSchemaMigrator.apply(jdbcUrl, migrationDirectory, 1));
      var migration = migrationDirectory.resolve("V001__core_data_contract.sql");
      Files.writeString(migration, Files.readString(migration) + "\n-- changed\n");
      assertThrows(
          IllegalStateException.class,
          () -> PostgresSchemaMigrator.apply(jdbcUrl, migrationDirectory, 1));
    } finally {
      dropSchema(testSchema);
    }
  }

  @Test
  void publishesAndReadsACompleteLexiconEntryThroughPostgres() throws Exception {
    var dataSource = new HikariDataSource();
    dataSource.setJdbcUrl(isolatedJdbcUrl);
    try (dataSource;
        var persistence = PostgresPersistence.open(isolatedJdbcUrl)) {
      try (var connection = dataSource.getConnection()) {
        assertTrue(connection.getMetaData().getDatabaseProductName().contains("PostgreSQL"));
      }
      var repository = persistence.repository();
      assertEquals(0, repository.publishedVersion());
      var published = repository.publish(request());
      assertEquals(1, published);
      assertEquals(1, repository.publishedVersion());
      var entry =
          repository
              .findByForms(published, List.of("reliable", "dependable", "reliably"))
              .getFirst();
      assertEquals("reliable", entry.lemma());
      assertEquals(
          List.of("dependable"),
          entry.aliases().stream().map(value -> value.normalizedForm()).toList());
      assertEquals(
          List.of("reliably"),
          entry.inflections().stream().map(value -> value.normalizedForm()).toList());
      assertEquals(
          List.of("可靠的"), entry.senses().stream().map(value -> value.chineseGloss()).toList());
      assertEquals(
          List.of("reliable"),
          repository.findPrewarmCandidates(published, 1).stream()
              .map(value -> value.lemma())
              .toList());
    }
  }

  private static String requiredJdbcUrl() {
    var value = System.getProperty(JDBC_URL_PROPERTY, "").trim();
    if (value.isEmpty()) {
      throw new IllegalStateException(
          "PostgreSQL integration runner requires explicit isolated " + JDBC_URL_PROPERTY);
    }
    return value;
  }

  private static String requiredMigrationsDirectory() {
    var value = System.getProperty(MIGRATIONS_PROPERTY, "").trim();
    if (value.isEmpty()) {
      throw new IllegalStateException(
          "PostgreSQL integration runner requires " + MIGRATIONS_PROPERTY);
    }
    return value;
  }

  private static String withSchema(String jdbcUrl, String value) {
    return jdbcUrl + (jdbcUrl.contains("?") ? "&" : "?") + "currentSchema=" + value;
  }

  private static String createSchema() throws SQLException {
    var value = "lexiflow_test_" + UUID.randomUUID().toString().replace("-", "");
    try (var connection = DriverManager.getConnection(adminJdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA \"" + value + "\"");
    }
    return value;
  }

  private static void dropSchema(String value) throws SQLException {
    try (var connection = DriverManager.getConnection(adminJdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("DROP SCHEMA IF EXISTS \"" + value + "\" CASCADE");
    }
  }

  private static Path copyMigrations(Path temporaryDirectory) throws IOException {
    var source = Path.of(requiredMigrationsDirectory());
    var target = temporaryDirectory.resolve("migrations");
    Files.createDirectories(target);
    try (var entries = Files.list(source)) {
      for (var entry : entries.toList()) {
        Files.copy(entry, target.resolve(entry.getFileName()));
      }
    }
    return target;
  }

  private static String scalar(String jdbcUrl, String sql) throws SQLException {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement();
        var rows = statement.executeQuery(sql)) {
      assertTrue(rows.next());
      return rows.getString(1);
    }
  }

  private static LexiconImportRequest request() {
    var source = new SourceReference("fixture", "MIT", "row-1");
    var row =
        new LexiconImportRow(
            "reliable",
            "可靠的",
            "worthy of trust",
            List.of("dependable"),
            List.of("reliably"),
            new LexiconPriority(4.2, 1, 900),
            source,
            source,
            List.of(source),
            true);
    return new LexiconImportRequest(
        List.of(row), new LexiconImportMetadata("a".repeat(64), "fixture", "MIT", Instant.EPOCH));
  }
}
