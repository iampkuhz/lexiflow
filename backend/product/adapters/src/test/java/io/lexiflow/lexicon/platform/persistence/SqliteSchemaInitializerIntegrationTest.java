package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 使用真实 SQLite JDBC 验证 schema 创建、STRICT 布局与目标保护。 */
class SqliteSchemaInitializerIntegrationTest {
  @TempDir Path temporary;

  @Test
  void initializesMissingFileAllObjectsAndStrictTablesThenRebuildsOwnedTables() throws Exception {
    Path file = temporary.toRealPath().resolve("facts.db");
    String url = SqliteTestSupport.url(file);
    SqliteSchemaInitializer.initialize(url, SqliteTestSupport.schemaFile());
    assertTrue(Files.isRegularFile(file));
    try (var connection = SqlitePersistence.connect(url)) {
      assertEquals(
          3,
          SqliteTestSupport.scalar(
              connection,
              "SELECT count(*) FROM pragma_table_list WHERE name LIKE 'lexicon_%' AND strict=1"));
      assertEquals(
          1,
          SqliteTestSupport.scalar(
              connection,
              "SELECT count(*) FROM sqlite_schema WHERE type='view' AND name='lexicon_lookup'"));
      assertEquals(
          3,
          SqliteTestSupport.scalar(
              connection,
              "SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND name LIKE 'lexicon_%_guard'"));
      SqliteSchemaInitializer.rebuild(url, SqliteTestSupport.schemaFile());
      assertEquals(10, SqliteSchemaInitializer.inspect(url).lexiconRelations().size());
      try (var statement = connection.createStatement()) {
        statement.execute("CREATE TABLE unrelated(value TEXT) STRICT");
      }
      assertEquals(List.of("unrelated"), SqliteSchemaInitializer.inspect(url).otherObjects());
      assertThrows(
          IllegalStateException.class,
          () -> SqliteSchemaInitializer.rebuild(url, SqliteTestSupport.schemaFile()));
      assertEquals(
          1,
          SqliteTestSupport.scalar(
              connection, "SELECT count(*) FROM sqlite_schema WHERE name='unrelated'"));
    }
    assertThrows(
        java.sql.SQLException.class,
        () -> SqliteSchemaInitializer.initialize(url, SqliteTestSupport.schemaFile()));
  }

  @Test
  void rejectsUnknownLexiconObjectsWithoutDeletingThem() throws Exception {
    Path file = temporary.toRealPath().resolve("protected.db");
    SqliteTestSupport.initialize(file);
    String url = SqliteTestSupport.url(file);
    try (var connection = SqlitePersistence.connect(url)) {
      try (var statement = connection.createStatement()) {
        statement.execute("CREATE TABLE lexicon_unowned(value TEXT) STRICT");
      }
    }
    assertThrows(
        IllegalStateException.class,
        () -> SqliteSchemaInitializer.rebuild(url, SqliteTestSupport.schemaFile()));
    try (var connection = SqlitePersistence.connect(url)) {
      assertEquals(
          1,
          SqliteTestSupport.scalar(
              connection, "SELECT count(*) FROM sqlite_schema WHERE name='lexicon_unowned'"));
      assertEquals(
          1,
          SqliteTestSupport.scalar(
              connection, "SELECT count(*) FROM sqlite_schema WHERE name='lexicon_entry'"));
    }
  }

  @Test
  void schemaScriptFailureRollsBackAllCreatedObjects() throws Exception {
    Path file = temporary.toRealPath().resolve("rollback.db");
    Path sql = temporary.toRealPath().resolve("broken.sql");
    Files.writeString(
        sql, "CREATE TABLE one(value TEXT) STRICT; CREATE INDEX broken ON missing(value);");
    assertThrows(
        Exception.class,
        () -> SqliteSchemaInitializer.initialize(SqliteTestSupport.url(file), sql));
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(file))) {
      assertEquals(
          0,
          SqliteTestSupport.scalar(
              connection, "SELECT count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"));
    }
  }
}
