package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** SQLite package 初始化、幂等重用、文件目标保护与原子回滚。 */
@Tag("sqlite")
class SqliteDatasetPackageIntegrationTest {
  @TempDir Path temporary;

  @Test
  void initializesExistingTargetAndRepeatsOnlyForExactPublishedSnapshot() throws Exception {
    var published = sourcePackage();
    Path target = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.createEmpty(target);
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(target))) {
      SqliteDatasetPackage.initialize(
          connection,
          published.zip,
          published.sha,
          target.toRealPath().toString(),
          published.schema);
      var receipt = SqliteDatasetVerifier.verify(connection, SqliteTestSupport.schema());
      assertEquals(published.version, receipt.datasetVersion());
      assertEquals(4, receipt.formCount());
      SqliteDatasetPackage.initialize(
          connection,
          published.zip,
          published.sha,
          target.toRealPath().toString(),
          published.schema);
      assertEquals(4, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_lookup"));
      assertEquals(2, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_entry"));
    }
    Path wrong = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.createEmpty(wrong);
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(wrong))) {
      assertThrows(
          Exception.class,
          () ->
              SqliteDatasetPackage.initialize(
                  connection,
                  published.zip,
                  published.sha,
                  target.toRealPath().toString(),
                  published.schema));
      assertEquals(
          0,
          SqliteTestSupport.scalar(
              connection, "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"));
    }
  }

  @Test
  void refusesAttachedDatabaseAndTemporaryUserObjects() throws Exception {
    var published = sourcePackage();
    Path target = SqliteTestSupport.newDatabase(temporary);
    Path attached = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.createEmpty(target);
    SqliteTestSupport.createEmpty(attached);
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(target))) {
      String attachedPath = attached.toRealPath().toString().replace("'", "''");
      try (var statement = connection.createStatement()) {
        statement.execute("ATTACH DATABASE '" + attachedPath + "' AS extra");
      }
      assertThrows(
          Exception.class,
          () ->
              SqliteDatasetPackage.initialize(
                  connection,
                  published.zip,
                  published.sha,
                  target.toRealPath().toString(),
                  published.schema));
      try (var statement = connection.createStatement()) {
        statement.execute("DETACH DATABASE extra");
        statement.execute("CREATE TEMP TABLE unexpected(value TEXT)");
      }
      assertThrows(
          Exception.class,
          () ->
              SqliteDatasetPackage.initialize(
                  connection,
                  published.zip,
                  published.sha,
                  target.toRealPath().toString(),
                  published.schema));
      assertEquals(
          0,
          SqliteTestSupport.scalar(
              connection, "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"));
    }
  }

  @Test
  void failedRestoreRollsBackCreatedSchemaAndRows() throws Exception {
    var published = sourcePackage();
    Path target = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.createEmpty(target);
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(target))) {
      assertThrows(
          IllegalStateException.class,
          () ->
              SqliteDatasetPackage.initialize(
                  connection,
                  published.zip,
                  published.sha,
                  target.toRealPath().toString(),
                  published.schema,
                  () -> {
                    throw new IllegalStateException("injected restore failure");
                  }));
      assertEquals(
          0,
          SqliteTestSupport.scalar(
              connection, "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"));
      SqliteDatasetPackage.initialize(
          connection,
          published.zip,
          published.sha,
          target.toRealPath().toString(),
          published.schema);
      assertEquals(
          published.version,
          SqliteDatasetVerifier.verify(connection, SqliteTestSupport.schema()).datasetVersion());
    }
  }

  @Test
  void keepsOriginalFailureAfterEngineHasAlreadyRolledBack() throws Exception {
    var published = sourcePackage();
    Path target = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.createEmpty(target);
    var original = new IllegalStateException("original publication failure");
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(target))) {
      var failure =
          assertThrows(
              IllegalStateException.class,
              () ->
                  SqliteDatasetPackage.initialize(
                      connection,
                      published.zip,
                      published.sha,
                      target.toRealPath().toString(),
                      published.schema,
                      () -> {
                        // 模拟磁盘耗尽后引擎自动回滚，而连接仍保留事务模式的情况。
                        try (var statement = connection.createStatement()) {
                          statement.execute("ROLLBACK");
                        } catch (java.sql.SQLException exception) {
                          throw new IllegalStateException(exception);
                        }
                        throw original;
                      }));
      assertSame(original, failure);
      assertTrue(failure.getSuppressed().length >= 1);
    }
  }

  @Test
  void importerEnforcesForeignKeysAndCanonicalFormGuard() throws Exception {
    var published = sourcePackage();
    Path target = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.createEmpty(target);
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(target))) {
      SqliteDatasetPackage.initialize(
          connection,
          published.zip,
          published.sha,
          target.toRealPath().toString(),
          published.schema);
      assertThrows(
          Exception.class,
          () ->
              execute(
                  connection,
                  "INSERT INTO lexicon_form(normalized_form,entry_id) VALUES('not-a-row',999)"));
      assertThrows(
          Exception.class,
          () ->
              execute(
                  connection,
                  "INSERT INTO lexicon_form(normalized_form,entry_id) "
                      + "SELECT lemma,entry_id FROM lexicon_entry WHERE canonical_form=1 LIMIT 1"));
      assertEquals(4, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_lookup"));
      assertThrows(
          Exception.class,
          () ->
              execute(
                  connection,
                  "INSERT INTO lexicon_entry(entry_id,lemma,gloss,canonical_form,priority) "
                      + "VALUES(9001,'reliable','重复',1,10)"));
      assertThrows(
          Exception.class,
          () ->
              execute(
                  connection,
                  "INSERT INTO lexicon_entry(entry_id,lemma,gloss,canonical_form,priority) "
                      + "VALUES(9002,'strict','严格',1,'not-integer')"));
      assertThrows(
          Exception.class,
          () ->
              execute(
                  connection,
                  "UPDATE lexicon_entry SET lemma='dependable' WHERE lemma='reliable'"));
      var invalidFlag = new LinkedHashMap<String, Object>();
      invalidFlag.put("entry_id", 9003L);
      invalidFlag.put("lemma", "outside");
      invalidFlag.put("gloss", "窗外");
      invalidFlag.put("canonical_form", 0L);
      invalidFlag.put("priority", 10L);
      assertThrows(
          java.io.IOException.class,
          () -> DatasetPackageCodec.validateRow(invalidFlag, DatasetPackageTables.ENTRY));
    }
  }

  private static void execute(java.sql.Connection connection, String sql) throws Exception {
    try (var statement = connection.createStatement()) {
      statement.executeUpdate(sql);
    }
  }

  private Package sourcePackage() throws Exception {
    Path source = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(source);
    long version =
        SqliteTestSupport.publish(
            source,
            SqliteTestSupport.row("reliable", "可靠的", List.of("dependable"), List.of("reliably")),
            SqliteTestSupport.row("reliably", "可靠地", List.of(), List.of()));
    Path zip = temporary.toRealPath().resolve("package-" + System.nanoTime() + ".zip");
    SqliteDatasetExport.Result exported;
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(source))) {
      exported =
          SqliteDatasetExport.export(
              connection, zip, SqliteTestSupport.approval(temporary), SqliteTestSupport.schema());
    }
    assertEquals(version, exported.datasetVersion());
    return new Package(zip, exported.sha256(), version, SqliteTestSupport.schema());
  }

  private record Package(Path zip, String sha, long version, byte[] schema) {}
}
