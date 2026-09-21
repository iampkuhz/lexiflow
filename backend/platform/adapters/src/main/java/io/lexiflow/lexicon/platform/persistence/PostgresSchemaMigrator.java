package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Pattern;

/**
 * 按固定顺序执行不可变的 PostgreSQL schema migration。
 *
 * <p>调用者必须显式提供 JDBC 目标，迁移器不会发现、启动或修改开发数据库。
 */
public final class PostgresSchemaMigrator {
  private static final Pattern MIGRATION_NAME = Pattern.compile("V([0-9]+)__([a-z0-9_]+)\\.sql");
  private static final String LEDGER_SQL =
      """
      CREATE TABLE IF NOT EXISTS schema_migration (
          version BIGINT PRIMARY KEY,
          name TEXT NOT NULL,
          checksum CHAR(64) NOT NULL,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
      """;

  private PostgresSchemaMigrator() {}

  /**
   * 对显式 JDBC 目标应用尚未记录的 migration，并锁定已应用文件的摘要。
   *
   * @param jdbcUrl 含义：只允许 PostgreSQL 的 JDBC 地址；取值范围：以 {@code jdbc:postgresql://} 开头
   * @param directory 含义：按 {@code VNNN__name.sql} 命名的迁移目录；取值范围：存在的本机目录
   * @param targetVersion 含义：可选的最高目标版本；空值表示全部版本；取值范围：1 至最新版本或空值
   * @return 本次事务实际应用的版本号
   * @throws SQLException 数据库拒绝 migration 或事务回滚时抛出
   * @throws IOException 无法读取迁移目录或文件时抛出
   */
  public static List<Integer> apply(String jdbcUrl, Path directory, Integer targetVersion)
      throws SQLException, IOException {
    if (!jdbcUrl.startsWith("jdbc:postgresql://")) {
      throw new IllegalArgumentException("migration JDBC URL must use jdbc:postgresql://");
    }
    var available = migrations(directory);
    var highest = available.getLast().version();
    if (targetVersion != null && (targetVersion < 1 || targetVersion > highest)) {
      throw new IllegalArgumentException("target version is outside available migrations");
    }
    try (var connection = DriverManager.getConnection(jdbcUrl)) {
      ensureLedger(connection);
      var applied = readLedger(connection);
      validateHistory(available, applied);
      var executed = new ArrayList<Integer>();
      for (var migration : available) {
        if (targetVersion != null && migration.version() > targetVersion) {
          break;
        }
        if (!applied.containsKey(migration.version())) {
          applyOne(connection, migration);
          executed.add(migration.version());
        }
      }
      return List.copyOf(executed);
    }
  }

  static List<Migration> migrations(Path directory) throws IOException {
    if (!Files.isDirectory(directory)) {
      throw new IllegalArgumentException("migration directory is unavailable: " + directory);
    }
    try (var paths = Files.list(directory)) {
      var migrations =
          paths
              .filter(Files::isRegularFile)
              .map(PostgresSchemaMigrator::migration)
              .sorted(Comparator.comparingInt(Migration::version))
              .toList();
      if (migrations.isEmpty()) {
        throw new IllegalArgumentException("migration directory is empty");
      }
      for (var index = 0; index < migrations.size(); index++) {
        var expected = index + 1;
        if (migrations.get(index).version() != expected) {
          throw new IllegalArgumentException(
              "migration versions must be contiguous; expected V%03d".formatted(expected));
        }
      }
      return migrations;
    }
  }

  private static Migration migration(Path path) {
    var matcher = MIGRATION_NAME.matcher(path.getFileName().toString());
    if (!matcher.matches()) {
      throw new IllegalArgumentException("invalid migration filename: " + path.getFileName());
    }
    try {
      return new Migration(
          Integer.parseInt(matcher.group(1)),
          matcher.group(2),
          path,
          sha256(Files.readAllBytes(path)));
    } catch (IOException exception) {
      throw new IllegalArgumentException("cannot read migration: " + path, exception);
    }
  }

  private static void ensureLedger(Connection connection) throws SQLException {
    try (var statement = connection.createStatement()) {
      statement.execute(LEDGER_SQL);
    }
  }

  private static Map<Integer, AppliedMigration> readLedger(Connection connection)
      throws SQLException {
    var result = new TreeMap<Integer, AppliedMigration>();
    try (var statement =
            connection.prepareStatement(
                "SELECT version, name, checksum FROM schema_migration ORDER BY version");
        var rows = statement.executeQuery()) {
      while (rows.next()) {
        result.put(
            rows.getInt("version"),
            new AppliedMigration(rows.getString("name"), rows.getString("checksum")));
      }
    }
    return result;
  }

  private static void validateHistory(
      List<Migration> available, Map<Integer, AppliedMigration> applied) {
    var availableByVersion =
        available.stream()
            .collect(java.util.stream.Collectors.toMap(Migration::version, value -> value));
    for (var entry : applied.entrySet()) {
      var migration = availableByVersion.get(entry.getKey());
      if (migration == null) {
        throw new IllegalStateException(
            "applied migration V%03d is unavailable".formatted(entry.getKey()));
      }
      var stored = entry.getValue();
      if (!migration.name().equals(stored.name())
          || !migration.checksum().equals(stored.checksum())) {
        throw new IllegalStateException(
            "applied migration V%03d checksum or name changed".formatted(entry.getKey()));
      }
    }
  }

  private static void applyOne(Connection connection, Migration migration)
      throws SQLException, IOException {
    var originalAutoCommit = connection.getAutoCommit();
    try {
      connection.setAutoCommit(false);
      try (var statement = connection.createStatement()) {
        statement.execute(Files.readString(migration.path(), StandardCharsets.UTF_8));
      }
      try (var ledger =
          connection.prepareStatement(
              "INSERT INTO schema_migration (version, name, checksum) VALUES (?, ?, ?)")) {
        ledger.setInt(1, migration.version());
        ledger.setString(2, migration.name());
        ledger.setString(3, migration.checksum());
        ledger.executeUpdate();
      }
      connection.commit();
    } catch (SQLException | IOException exception) {
      connection.rollback();
      throw exception;
    } finally {
      connection.setAutoCommit(originalAutoCommit);
    }
  }

  private static String sha256(byte[] content) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }

  /**
   * 已解析的不可变 migration 文件及其摘要。
   *
   * @param version 含义：连续的 migration 版本号
   * @param name 含义：文件名中的稳定业务名称
   * @param path 含义：只读 SQL 文件的绝对路径
   * @param checksum 含义：SQL 字节的 SHA-256 摘要
   */
  record Migration(int version, String name, Path path, String checksum) {}

  /**
   * schema_migration 中已经锁定的版本身份。
   *
   * @param name 含义：已执行 migration 的稳定名称
   * @param checksum 含义：执行时锁定的 SHA-256 摘要
   */
  private record AppliedMigration(String name, String checksum) {}
}
