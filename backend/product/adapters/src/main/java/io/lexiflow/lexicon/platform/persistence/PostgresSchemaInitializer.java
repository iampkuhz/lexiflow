package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * 对显式 PostgreSQL 目标执行最新 SQL，支持空 schema 初始化和受保护的词库表重建。
 *
 * <p>普通初始化拒绝非空 schema；重建只删除本项目表。失败全部回滚，调用者必须显式提供 JDBC 目标。
 */
public final class PostgresSchemaInitializer {

  private static final Set<String> OWNED_TABLES =
      Set.of("lexicon_dataset", "lexicon_prepared_entry", "lexicon_hint_lookup");

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
    var sql = readSql(jdbcUrl, schemaFile);
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

  /**
   * 返回目标数据库、schema 和本项目关系，用于重建前向执行人展示准确目标。
   *
   * @param jdbcUrl 含义：显式目标地址；取值范围：PostgreSQL JDBC 地址
   * @return 不包含 JDBC 凭据的目标身份与本项目关系
   * @throws SQLException 无法读取目标元数据时抛出
   */
  public static Target inspect(String jdbcUrl) throws SQLException {
    requirePostgres(jdbcUrl);
    try (var connection = DriverManager.getConnection(jdbcUrl)) {
      return inspect(connection);
    }
  }

  /**
   * 只删除本项目词库表并在同一事务中创建最新结构，不删除整个 schema 或本机文件。
   *
   * @param jdbcUrl 含义：显式开发库地址；取值范围：PostgreSQL JDBC 地址
   * @param schemaFile 含义：最新结构 SQL；取值范围：可读取的本机文件
   * @throws SQLException 目标、删除或创建失败时抛出，事务回滚
   * @throws IOException 无法读取 SQL 时抛出
   */
  public static void rebuild(String jdbcUrl, Path schemaFile) throws SQLException, IOException {
    var sql = readSql(jdbcUrl, schemaFile);
    try (var connection = DriverManager.getConnection(jdbcUrl)) {
      var originalAutoCommit = connection.getAutoCommit();
      try {
        connection.setAutoCommit(false);
        var target = inspect(connection);
        var schema = quoteIdentifier(target.schema());
        try (var statement = connection.createStatement()) {
          // 不使用 CASCADE：其他对象依赖本项目表时由 PostgreSQL 拒绝并回滚。
          statement.execute(
              "DROP TABLE IF EXISTS "
                  + schema
                  + ".lexicon_hint_lookup, "
                  + schema
                  + ".lexicon_prepared_entry, "
                  + schema
                  + ".lexicon_dataset");
          statement.execute(sql);
        }
        connection.commit();
      } catch (SQLException | RuntimeException exception) {
        connection.rollback();
        throw exception;
      } finally {
        connection.setAutoCommit(originalAutoCommit);
      }
    }
  }

  private static String readSql(String jdbcUrl, Path schemaFile) throws IOException {
    requirePostgres(jdbcUrl);
    if (!Files.isRegularFile(schemaFile)) {
      throw new IllegalArgumentException("schema file is unavailable: " + schemaFile);
    }
    return Files.readString(schemaFile, StandardCharsets.UTF_8);
  }

  private static void requirePostgres(String jdbcUrl) {
    if (jdbcUrl == null || !jdbcUrl.startsWith("jdbc:postgresql://")) {
      throw new IllegalArgumentException("init JDBC URL must use jdbc:postgresql://");
    }
  }

  private static Target inspect(Connection connection) throws SQLException {
    String database;
    String schema;
    try (var statement = connection.createStatement();
        var rows = statement.executeQuery("SELECT current_database(), current_schema()")) {
      rows.next();
      database = rows.getString(1);
      schema = rows.getString(2);
    }
    if (schema == null || schema.isBlank()) {
      throw new IllegalStateException("JDBC target has no current schema");
    }
    var relations = new ArrayList<String>();
    try (var statement =
        connection.prepareStatement(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                + "WHERE n.nspname=? AND c.relname LIKE 'lexicon\\_%' ESCAPE '\\' "
                + "AND c.relkind IN ('r','p','v','m','f','S') ORDER BY c.relname")) {
      statement.setString(1, schema);
      try (var rows = statement.executeQuery()) {
        while (rows.next()) relations.add(rows.getString(1));
      }
    }
    var unexpected = relations.stream().filter(name -> !OWNED_TABLES.contains(name)).toList();
    if (!unexpected.isEmpty()) {
      throw new IllegalStateException("refusing unknown lexicon relations: " + unexpected);
    }
    return new Target(database, schema, List.copyOf(relations));
  }

  private static String quoteIdentifier(String value) {
    return "\"" + value.replace("\"", "\"\"") + "\"";
  }

  /**
   * 重建确认界面只显示数据库身份，不输出可能包含凭据的 JDBC URL。
   *
   * @param database 含义：目标数据库名；取值范围：当前连接的数据库
   * @param schema 含义：目标 schema 名；取值范围：当前连接的 schema
   * @param lexiconRelations 含义：已有本项目关系；取值范围：已排序且不可变的关系名列表
   */
  public record Target(String database, String schema, List<String> lexiconRelations) {}

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
