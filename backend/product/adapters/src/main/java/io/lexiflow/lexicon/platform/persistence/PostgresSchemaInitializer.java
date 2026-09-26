package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

/**
 * 对显式 PostgreSQL 目标的空 schema 在一个事务中执行最新 SQL。
 *
 * <p>非空 schema 拒绝且不修改；失败全部回滚。调用者必须显式提供 JDBC 目标，初始化器不会发现、 启动数据库或隐式清库。
 */
public final class PostgresSchemaInitializer {

  private PostgresSchemaInitializer() {}

  /**
   * 在空 schema 中执行最新初始化 SQL。
   *
   * @param jdbcUrl 含义：只允许 PostgreSQL 的 JDBC 地址；取值范围：以 {@code jdbc:postgresql://} 开头
   * @param schemaFile 含义：最新 SQL 文件；取值范围：存在的本机文件
   * @throws SQLException 数据库拒绝或 schema 非空时抛出
   * @throws IOException 无法读取 SQL 文件时抛出
   */
  public static void initialize(String jdbcUrl, Path schemaFile) throws SQLException, IOException {
    if (jdbcUrl == null || !jdbcUrl.startsWith("jdbc:postgresql://")) {
      throw new IllegalArgumentException("init JDBC URL must use jdbc:postgresql://");
    }
    if (!Files.isRegularFile(schemaFile)) {
      throw new IllegalArgumentException("schema file is unavailable: " + schemaFile);
    }
    var sql = Files.readString(schemaFile, StandardCharsets.UTF_8);
    try (var connection = DriverManager.getConnection(jdbcUrl)) {
      if (isSchemaNonEmpty(connection)) {
        throw new IllegalStateException("refusing to initialize a non-empty schema");
      }
      var originalAutoCommit = connection.getAutoCommit();
      try {
        connection.setAutoCommit(false);
        try (var statement = connection.createStatement()) {
          statement.execute(sql);
        }
        connection.commit();
      } catch (SQLException exception) {
        connection.rollback();
        throw exception;
      } finally {
        connection.setAutoCommit(originalAutoCommit);
      }
    }
  }

  private static boolean isSchemaNonEmpty(Connection connection) throws SQLException {
    try (var statement =
            connection.prepareStatement(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_depend "
                    + "WHERE refclassid = 'pg_catalog.pg_namespace'::regclass "
                    + "AND refobjid = current_schema()::regnamespace)");
        var rows = statement.executeQuery()) {
      rows.next();
      return rows.getBoolean(1);
    }
  }
}
