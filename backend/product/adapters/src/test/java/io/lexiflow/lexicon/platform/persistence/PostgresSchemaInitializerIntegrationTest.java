package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.UUID;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/** 隔离 PostgreSQL schema 验证重建只处理本项目对象且失败回滚。 */
@Tag("postgres")
class PostgresSchemaInitializerIntegrationTest {

  @Test
  void rebuildsOwnedTablesAndPreservesUnrelatedTable() throws Exception {
    withSchema(
        jdbcUrl -> {
          var schemaFile = schemaFile();
          PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
          execute(jdbcUrl, "CREATE TABLE unrelated_project (id integer)");
          execute(jdbcUrl, "INSERT INTO unrelated_project VALUES (7)");
          assertEquals(3, PostgresSchemaInitializer.inspect(jdbcUrl).lexiconRelations().size());
          PostgresSchemaInitializer.rebuild(jdbcUrl, schemaFile);
          assertEquals("7", scalar(jdbcUrl, "SELECT id::text FROM unrelated_project"));
          assertEquals(3, PostgresSchemaInitializer.inspect(jdbcUrl).lexiconRelations().size());
          assertEquals("0", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_dataset"));
        });
  }

  @Test
  void unknownLexiconRelationAndExternalDependencyBlockDeletion() throws Exception {
    withSchema(
        jdbcUrl -> {
          var schemaFile = schemaFile();
          PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
          execute(jdbcUrl, "CREATE TABLE lexicon_other (id integer)");
          assertThrows(
              IllegalStateException.class,
              () -> PostgresSchemaInitializer.rebuild(jdbcUrl, schemaFile));
          assertEquals(
              "3",
              scalar(
                  jdbcUrl,
                  "SELECT COUNT(*)::text FROM information_schema.tables "
                      + "WHERE table_schema=current_schema() AND table_name IN "
                      + "('lexicon_dataset','lexicon_entry','lexicon_form')"));
          execute(jdbcUrl, "DROP TABLE lexicon_other");
          execute(jdbcUrl, "CREATE VIEW unrelated_view AS SELECT dataset_id FROM lexicon_dataset");
          assertThrows(
              Exception.class, () -> PostgresSchemaInitializer.rebuild(jdbcUrl, schemaFile));
          assertEquals("0", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_dataset"));
          assertEquals("0", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM unrelated_view"));
        });
  }

  @Test
  void refusesOrdinaryInitializationOnOldShapeButExplicitRebuildDropsOnlyOwnedTables()
      throws Exception {
    withSchema(
        jdbcUrl -> {
          execute(jdbcUrl, "CREATE TABLE lexicon_dataset (dataset_id smallint PRIMARY KEY)");
          execute(
              jdbcUrl, "CREATE TABLE lexicon_prepared_entry (lexicon_entry_id uuid PRIMARY KEY)");
          execute(
              jdbcUrl,
              "CREATE TABLE lexicon_hint_lookup (lexicon_entry_id uuid REFERENCES lexicon_prepared_entry(lexicon_entry_id))");
          var schemaFile = schemaFile();
          assertThrows(
              IllegalStateException.class,
              () -> PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile));
          PostgresSchemaInitializer.rebuild(jdbcUrl, schemaFile);
          assertEquals(
              "3",
              scalar(
                  jdbcUrl,
                  "SELECT COUNT(*)::text FROM information_schema.tables "
                      + "WHERE table_schema=current_schema() AND table_name IN ('lexicon_dataset','lexicon_entry','lexicon_form')"));
          assertEquals(
              "0",
              scalar(
                  jdbcUrl,
                  "SELECT COUNT(*)::text FROM information_schema.tables "
                      + "WHERE table_schema=current_schema() AND table_name IN ('lexicon_prepared_entry','lexicon_hint_lookup')"));
        });
  }

  private static Path schemaFile() {
    return Path.of(System.getProperty("lexiflow.postgres.schema.file"));
  }

  private static void withSchema(CheckedJdbc action) throws Exception {
    var admin = System.getProperty("lexiflow.postgres.test.jdbcUrl");
    assertTrue(admin != null && !admin.isBlank());
    var schema = "lexiflow_rebuild_" + UUID.randomUUID().toString().replace("-", "");
    try (var connection = DriverManager.getConnection(admin);
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA \"" + schema + "\"");
    }
    try {
      action.run(admin + (admin.contains("?") ? "&" : "?") + "currentSchema=" + schema);
    } finally {
      try (var connection = DriverManager.getConnection(admin);
          var statement = connection.createStatement()) {
        statement.execute("DROP SCHEMA IF EXISTS \"" + schema + "\" CASCADE");
      }
    }
  }

  private static void execute(String jdbcUrl, String sql) throws Exception {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute(sql);
    }
  }

  private static String scalar(String jdbcUrl, String sql) throws Exception {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement();
        var rows = statement.executeQuery(sql)) {
      assertTrue(rows.next());
      return rows.getString(1);
    }
  }

  @FunctionalInterface
  private interface CheckedJdbc {
    void run(String jdbcUrl) throws Exception;
  }
}
