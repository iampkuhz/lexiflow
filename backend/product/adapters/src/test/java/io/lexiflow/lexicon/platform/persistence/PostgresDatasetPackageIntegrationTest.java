package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 隔离 PostgreSQL 验证目标归属、初始化、精确幂等与完整事务回滚。 */
@Tag("postgres")
class PostgresDatasetPackageIntegrationTest {
  @TempDir Path temporary;

  @Test
  void oneApiJarExportsVerifiesAndInitializesWithoutStartingHttp() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var target = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      Path zip = dir.resolve("release.zip");
      Path approval = PostgresDatasetExportIntegrationTest.approval(dir);
      var exported =
          cli(
              source.jdbcUrl(),
              "export",
              "--output",
              zip.toString(),
              "--approval-file",
              approval.toString());
      var result =
          DatasetPackageCodec.object(
              DatasetPackageCodec.parseJson(exported),
              java.util.Set.of("result", "sha256", "datasetVersion"));
      assertEquals("PASS", result.get("result"));
      String digest = (String) result.get("sha256");
      assertEquals(DatasetPackageCodec.sha256(zip), digest);
      assertEquals(
          "{\"result\":\"PASS\"}\n",
          cli(null, "verify", "--package", zip.toString(), "--expected-sha256", digest));
      assertEquals(
          "{\"result\":\"PASS\"}\n",
          cli(
              target.jdbcUrl(),
              "initialize",
              "--package",
              zip.toString(),
              "--expected-sha256",
              digest,
              "--expected-database",
              target.database(),
              "--expected-schema",
              target.schemaName()));
      try (var persistence = PostgresPersistence.open(target.jdbcUrl())) {
        assertEquals(1, persistence.repository().publishedVersion());
        assertEquals(
            1, persistence.repository().findByForms(1, java.util.List.of("reliable")).size());
      }
    }
  }

  @Test
  void separateApiJarProcessesExportIdenticalBytesAcrossTimezones() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true)) {
      Path dir = temporary.toRealPath();
      Path approval = PostgresDatasetExportIntegrationTest.approval(dir);
      Path utc = dir.resolve("utc.zip");
      Path shanghai = dir.resolve("shanghai.zip");
      cliWithTimezone(
          source.jdbcUrl(),
          "UTC",
          "export",
          "--output",
          utc.toString(),
          "--approval-file",
          approval.toString());
      cliWithTimezone(
          source.jdbcUrl(),
          "Asia/Shanghai",
          "export",
          "--output",
          shanghai.toString(),
          "--approval-file",
          approval.toString());
      assertEquals(DatasetPackageCodec.sha256(utc), DatasetPackageCodec.sha256(shanghai));
    }
  }

  private static String cli(String jdbcUrl, String... args) throws Exception {
    return cliWithTimezone(jdbcUrl, null, args);
  }

  private static String cliWithTimezone(String jdbcUrl, String timezone, String... args)
      throws Exception {
    String jar = System.getProperty("lexiflow.release.test.bootJar", "");
    if (jar.isBlank()) throw new IllegalStateException("API boot JAR required");
    var command = new java.util.ArrayList<String>();
    command.add(Path.of(System.getProperty("java.home"), "bin", "java").toString());
    if (timezone != null) command.add("-Duser.timezone=" + timezone);
    command.add("-jar");
    command.add(jar);
    command.add("--release-dataset");
    command.addAll(java.util.List.of(args));
    var builder = new ProcessBuilder(command).redirectErrorStream(true);
    if (jdbcUrl == null) builder.environment().remove("LEXIFLOW_RELEASE_JDBC_URL");
    else builder.environment().put("LEXIFLOW_RELEASE_JDBC_URL", jdbcUrl);
    var process = builder.start();
    try {
      if (!process.waitFor(20, java.util.concurrent.TimeUnit.SECONDS)) {
        process.destroyForcibly();
        throw new IllegalStateException("CLI timeout");
      }
      String output =
          new String(
              process.getInputStream().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
      assertEquals(0, process.exitValue(), output);
      return output;
    } finally {
      process.destroy();
    }
  }

  @Test
  void trustedSchemaCatalogFingerprint() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var connection = DriverManager.getConnection(source.jdbcUrl())) {
      assertEquals(
          "755f260958f1eb5e805a8faa0d151fa8ea37295eee44afbb93421e2b113a8a57",
          PostgresDatasetPackage.shapeFingerprint(connection));
    }
  }

  @Test
  void initializesEmptySchemaAndAcceptsOnlyExactRepeat() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var target = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      byte[] sql = PostgresDatasetExportIntegrationTest.schema();
      Path packagePath = dir.resolve("package.zip");
      var result = exported(source, packagePath, dir, sql);
      try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
        PostgresDatasetPackage.initialize(
            connection, packagePath, result.sha256(), target.database(), target.schemaName(), sql);
        PostgresDatasetPackage.initialize(
            connection, packagePath, result.sha256(), target.database(), target.schemaName(), sql);
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_dataset"));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_prepared_entry"));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_hint_lookup"));
      }
      try (var connection = DriverManager.getConnection(target.jdbcUrl());
          var statement = connection.createStatement()) {
        statement.executeUpdate(
            "UPDATE lexicon_prepared_entry SET source_gloss='different' WHERE lemma='reliable'");
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetPackage.initialize(
                    connection,
                    packagePath,
                    result.sha256(),
                    target.database(),
                    target.schemaName(),
                    sql));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_dataset"));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_prepared_entry"));
      }
    }
  }

  @Test
  void rejectsCorruptionAndUnrelatedExistingObjectWithoutChangingIt() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var target = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      byte[] sql = PostgresDatasetExportIntegrationTest.schema();
      Path packagePath = dir.resolve("package.zip");
      var result = exported(source, packagePath, dir, sql);
      Path corrupt = dir.resolve("corrupt.zip");
      byte[] bytes = Files.readAllBytes(packagePath);
      bytes[bytes.length / 2] ^= 1;
      Files.write(corrupt, bytes);
      try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetPackage.initialize(
                    connection,
                    corrupt,
                    result.sha256(),
                    target.database(),
                    target.schemaName(),
                    sql));
        assertEquals(
            0,
            scalar(
                connection,
                "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n "
                    + "ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relkind='r'"));
        try (var statement = connection.createStatement()) {
          statement.execute("CREATE TABLE unrelated(id integer)");
          statement.execute("INSERT INTO unrelated VALUES (1)");
        }
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetPackage.initialize(
                    connection,
                    packagePath,
                    result.sha256(),
                    target.database(),
                    target.schemaName(),
                    sql));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM unrelated"));
      }
    }
  }

  @Test
  void injectedFailureRollsBackSchemaAndDataTogether() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var target = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      byte[] sql = PostgresDatasetExportIntegrationTest.schema();
      Path packagePath = dir.resolve("package.zip");
      var result = exported(source, packagePath, dir, sql);
      try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
        assertThrows(
            IllegalStateException.class,
            () ->
                PostgresDatasetPackage.initialize(
                    connection,
                    packagePath,
                    result.sha256(),
                    target.database(),
                    target.schemaName(),
                    sql,
                    () -> {
                      throw new IllegalStateException("synthetic interruption");
                    }));
        assertEquals(
            0,
            scalar(
                connection,
                "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n "
                    + "ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relkind='r'"));
      }
    }
  }

  @Test
  void rejectsChangedCheckAndIndexDefinitions() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var checkTarget = PostgresDatasetExportIntegrationTest.Fixture.create(false);
        var indexTarget = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      byte[] sql = PostgresDatasetExportIntegrationTest.schema();
      Path packagePath = dir.resolve("package.zip");
      var result = exported(source, packagePath, dir, sql);
      for (var target :
          new PostgresDatasetExportIntegrationTest.Fixture[] {checkTarget, indexTarget}) {
        try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
          PostgresDatasetPackage.initialize(
              connection,
              packagePath,
              result.sha256(),
              target.database(),
              target.schemaName(),
              sql);
        }
      }
      try (var connection = DriverManager.getConnection(checkTarget.jdbcUrl());
          var statement = connection.createStatement()) {
        statement.execute(
            "ALTER TABLE lexicon_prepared_entry DROP CONSTRAINT lexicon_prepared_entry_block_rule_ck");
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetPackage.initialize(
                    connection,
                    packagePath,
                    result.sha256(),
                    checkTarget.database(),
                    checkTarget.schemaName(),
                    sql));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_dataset"));
      }
      try (var connection = DriverManager.getConnection(indexTarget.jdbcUrl());
          var statement = connection.createStatement()) {
        statement.execute("DROP INDEX lexicon_hint_lookup_prewarm_idx");
        statement.execute(
            "CREATE INDEX lexicon_hint_lookup_prewarm_idx ON lexicon_hint_lookup(normalized_form)");
        assertThrows(
            IOException.class,
            () ->
                PostgresDatasetPackage.initialize(
                    connection,
                    packagePath,
                    result.sha256(),
                    indexTarget.database(),
                    indexTarget.schemaName(),
                    sql));
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_dataset"));
      }
    }
  }

  @Test
  void existingDatasetWaitsForOrdinaryWriterAndRejectsCommittedDifference() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var target = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      byte[] sql = PostgresDatasetExportIntegrationTest.schema();
      Path packagePath = dir.resolve("package.zip");
      var result = exported(source, packagePath, dir, sql);
      try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
        PostgresDatasetPackage.initialize(
            connection, packagePath, result.sha256(), target.database(), target.schemaName(), sql);
      }
      try (var writer = DriverManager.getConnection(target.jdbcUrl());
          var pool = java.util.concurrent.Executors.newSingleThreadExecutor()) {
        writer.setAutoCommit(false);
        try (var update = writer.createStatement()) {
          update.executeUpdate(
              "UPDATE lexicon_prepared_entry SET source_gloss='changed' WHERE lemma='reliable'");
        }
        var pid = new java.util.concurrent.atomic.AtomicInteger();
        var connected = new java.util.concurrent.CountDownLatch(1);
        var attempt =
            pool.submit(
                () -> {
                  try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
                    try (var statement = connection.createStatement();
                        var row = statement.executeQuery("SELECT pg_backend_pid()")) {
                      row.next();
                      pid.set(row.getInt(1));
                      connected.countDown();
                    }
                    PostgresDatasetPackage.initialize(
                        connection,
                        packagePath,
                        result.sha256(),
                        target.database(),
                        target.schemaName(),
                        sql);
                    return false;
                  } catch (IOException exception) {
                    return true;
                  }
                });
        assertTrue(connected.await(10, java.util.concurrent.TimeUnit.SECONDS));
        boolean waiting = false;
        long deadline = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(5);
        try (var monitor = DriverManager.getConnection(target.jdbcUrl());
            var query =
                monitor.prepareStatement(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        + "WHERE pid=? AND locktype='relation' AND mode='ShareLock' AND NOT granted)")) {
          query.setInt(1, pid.get());
          while (System.nanoTime() < deadline && !attempt.isDone()) {
            try (var row = query.executeQuery()) {
              row.next();
              waiting = row.getBoolean(1);
            }
            if (waiting) break;
            Thread.sleep(20);
          }
        }
        assertTrue(waiting, "reader must wait for SHARE table lock before comparison");
        assertThrows(
            java.util.concurrent.TimeoutException.class,
            () -> attempt.get(300, java.util.concurrent.TimeUnit.MILLISECONDS));
        writer.commit();
        assertTrue(attempt.get(20, java.util.concurrent.TimeUnit.SECONDS));
      }
      try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_dataset"));
      }
    }
  }

  @Test
  void concurrentInitializersSerializeAndBothObserveExactPackage() throws Exception {
    try (var source = PostgresDatasetExportIntegrationTest.Fixture.create(true);
        var target = PostgresDatasetExportIntegrationTest.Fixture.create(false)) {
      Path dir = temporary.toRealPath();
      byte[] sql = PostgresDatasetExportIntegrationTest.schema();
      Path packagePath = dir.resolve("package.zip");
      var result = exported(source, packagePath, dir, sql);
      var ready = new java.util.concurrent.CountDownLatch(2);
      var start = new java.util.concurrent.CountDownLatch(1);
      try (var pool = java.util.concurrent.Executors.newFixedThreadPool(2)) {
        var tasks = new java.util.ArrayList<java.util.concurrent.Future<?>>(2);
        for (int i = 0; i < 2; i++)
          tasks.add(
              pool.submit(
                  () -> {
                    try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
                      ready.countDown();
                      if (!start.await(10, java.util.concurrent.TimeUnit.SECONDS))
                        throw new IllegalStateException();
                      PostgresDatasetPackage.initialize(
                          connection,
                          packagePath,
                          result.sha256(),
                          target.database(),
                          target.schemaName(),
                          sql);
                    } catch (Exception exception) {
                      throw new IllegalStateException(exception);
                    }
                  }));
        assertTrue(ready.await(10, java.util.concurrent.TimeUnit.SECONDS));
        start.countDown();
        for (var task : tasks) task.get(30, java.util.concurrent.TimeUnit.SECONDS);
      }
      try (var connection = DriverManager.getConnection(target.jdbcUrl())) {
        assertEquals(1, scalar(connection, "SELECT COUNT(*) FROM lexicon_dataset"));
      }
    }
  }

  private static PostgresDatasetExport.Result exported(
      PostgresDatasetExportIntegrationTest.Fixture source, Path packagePath, Path dir, byte[] sql)
      throws Exception {
    try (var connection = DriverManager.getConnection(source.jdbcUrl())) {
      return PostgresDatasetExport.export(
          connection, packagePath, PostgresDatasetExportIntegrationTest.approval(dir), sql);
    }
  }

  private static long scalar(java.sql.Connection connection, String sql) throws SQLException {
    try (var statement = connection.createStatement();
        var result = statement.executeQuery(sql)) {
      assertTrue(result.next());
      return result.getLong(1);
    }
  }
}
