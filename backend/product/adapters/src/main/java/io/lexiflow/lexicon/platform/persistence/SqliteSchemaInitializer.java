package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** 对显式 SQLite 文件执行最新版 schema 初始化、检查与受保护的项目表重建。 */
public final class SqliteSchemaInitializer {
  private static final Set<String> OWNED_TABLES = SqliteDatasetPackage.RELATIONS;

  private SqliteSchemaInitializer() {}

  /**
   * 原子创建显式目标文件并执行最新结构，拒绝复用已有目标。
   *
   * @param jdbcUrl 含义：待创建的数据库地址；取值范围：规范父目录下不存在的绝对文件地址，不含只读选项
   * @param schemaFile 含义：最新可信结构脚本；取值范围：存在且可读的非空脚本文件
   */
  public static void initialize(String jdbcUrl, Path schemaFile) throws SQLException, IOException {
    var sql = readSql(jdbcUrl, schemaFile);
    try (var connection = openForInitialization(jdbcUrl)) {
      if (hasUserObjects(connection))
        throw new IllegalStateException("refusing to initialize a non-empty database file");
      connection.setAutoCommit(false);
      try {
        executeTrustedSql(connection, sql.getBytes(StandardCharsets.UTF_8));
        connection.commit();
      } catch (SQLException | RuntimeException failure) {
        connection.rollback();
        throw failure;
      } finally {
        connection.setAutoCommit(true);
      }
    }
  }

  /**
   * 为显式离线初始化原子创建数据库文件，普通产品连接不得使用此入口。
   *
   * @param jdbcUrl 含义：初始化目标地址；取值范围：规范父目录下不存在的绝对文件地址，不含打开已有文件的模式
   */
  public static Connection openForInitialization(String jdbcUrl) throws SQLException {
    SqlitePersistence.validateDatabaseUrl(jdbcUrl, false);
    if (jdbcUrl.contains("?mode=ro") || jdbcUrl.contains("?mode=rw")) {
      throw new IllegalArgumentException(
          "schema initialization requires a writable create-capable URL");
    }
    Path file = SqlitePersistence.databaseFilePath(jdbcUrl);
    boolean created = false;
    Object fileKey = null;
    try {
      Files.createFile(file);
      created = true;
      fileKey =
          Files.readAttributes(file, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS)
              .fileKey();
      return SqlitePersistence.connect(jdbcUrl);
    } catch (IOException | SQLException | RuntimeException failure) {
      if (created && fileKey != null) {
        try {
          Object currentKey =
              Files.readAttributes(file, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS)
                  .fileKey();
          if (fileKey.equals(currentKey)) Files.deleteIfExists(file);
        } catch (IOException cleanupFailure) {
          failure.addSuppressed(cleanupFailure);
        }
      }
      throw new SQLException("SQLite database file could not be created", failure);
    }
  }

  /**
   * 在调用者持有的事务中执行可信最新结构，保留触发器内部定界符。
   *
   * @param connection 含义：执行结构脚本的连接；取值范围：非空、已打开且由调用者管理事务
   * @param trustedSql 含义：可信最新结构的原始字节；取值范围：非空且包含合法结构语句的字节数组
   */
  public static void executeTrustedSql(Connection connection, byte[] trustedSql)
      throws SQLException {
    if (trustedSql == null || trustedSql.length == 0)
      throw new IllegalArgumentException("trusted SQL is required");
    executeSchema(connection, new String(trustedSql, StandardCharsets.UTF_8));
  }

  /**
   * 只读检查目标文件及其用户对象，不创建或修改数据库。
   *
   * @param jdbcUrl 含义：待检查的数据库地址；取值范围：已存在的绝对规范文件地址
   */
  public static Target inspect(String jdbcUrl) throws SQLException {
    try (var connection = SqlitePersistence.connect(jdbcUrl)) {
      return inspect(connection);
    }
  }

  /**
   * 只重建本项目拥有的最新表，删除与创建使用同一事务，拒绝未知对象。
   *
   * @param jdbcUrl 含义：显式重建目标；取值范围：已存在、允许写入的绝对规范文件地址
   * @param schemaFile 含义：最新可信结构脚本；取值范围：存在且可读的非空脚本文件
   */
  public static void rebuild(String jdbcUrl, Path schemaFile) throws SQLException, IOException {
    var sql = readSql(jdbcUrl, schemaFile);
    try (var connection = SqlitePersistence.connect(jdbcUrl)) {
      if (!connection.getAutoCommit())
        throw new IllegalStateException("rebuild requires a fresh connection");
      connection.setAutoCommit(false);
      try {
        var target = inspect(connection);
        if (!target.otherObjects().isEmpty()) {
          throw new IllegalStateException(
              "refusing database with non-owned objects: " + target.otherObjects());
        }
        for (String relation : target.lexiconRelations()) {
          if (!OWNED_TABLES.contains(relation)) {
            throw new IllegalStateException("refusing unknown lexicon relation: " + relation);
          }
        }
        try (var statement = connection.createStatement()) {
          if (target.lexiconRelations().contains("lexicon_lookup"))
            statement.execute("DROP VIEW lexicon_lookup");
          if (target.lexiconRelations().contains("lexicon_form_canonical_guard"))
            statement.execute("DROP TRIGGER lexicon_form_canonical_guard");
          if (target.lexiconRelations().contains("lexicon_form_canonical_update_guard"))
            statement.execute("DROP TRIGGER lexicon_form_canonical_update_guard");
          if (target.lexiconRelations().contains("lexicon_entry_canonical_update_guard"))
            statement.execute("DROP TRIGGER lexicon_entry_canonical_update_guard");
          for (String table : List.of("lexicon_form", "lexicon_entry", "lexicon_dataset")) {
            if (target.lexiconRelations().contains(table))
              statement.execute("DROP TABLE \"" + table + "\"");
          }
        }
        executeSchema(connection, sql);
        connection.commit();
      } catch (SQLException | RuntimeException failure) {
        connection.rollback();
        throw failure;
      } finally {
        connection.setAutoCommit(true);
      }
    }
  }

  private static void executeSchema(Connection connection, String sql) throws SQLException {
    try (var statement = connection.createStatement()) {
      for (String command : sqlStatements(sql)) statement.execute(command);
    }
  }

  private static List<String> sqlStatements(String sql) {
    var statements = new ArrayList<String>();
    var current = new StringBuilder();
    boolean singleQuoted = false;
    boolean doubleQuoted = false;
    boolean lineComment = false;
    boolean blockComment = false;
    for (int i = 0; i < sql.length(); i++) {
      char c = sql.charAt(i);
      char next = i + 1 < sql.length() ? sql.charAt(i + 1) : '\0';
      if (lineComment) {
        current.append(c);
        if (c == '\n') lineComment = false;
      } else if (blockComment) {
        current.append(c);
        if (c == '*' && next == '/') {
          current.append(next);
          i++;
          blockComment = false;
        }
      } else if (!singleQuoted && !doubleQuoted && c == '-' && next == '-') {
        current.append(c).append(next);
        i++;
        lineComment = true;
      } else if (!singleQuoted && !doubleQuoted && c == '/' && next == '*') {
        current.append(c).append(next);
        i++;
        blockComment = true;
      } else if (c == '\'' && !doubleQuoted) {
        current.append(c);
        if (singleQuoted && next == '\'') {
          current.append(next);
          i++;
        } else singleQuoted = !singleQuoted;
      } else if (c == '"' && !singleQuoted) {
        current.append(c);
        if (doubleQuoted && next == '"') {
          current.append(next);
          i++;
        } else doubleQuoted = !doubleQuoted;
      } else if (c == ';' && !singleQuoted && !doubleQuoted) {
        String pending = current.toString();
        boolean trigger = pending.matches("(?is).*\\bCREATE\\s+(?:TEMP\\s+)?TRIGGER\\b.*");
        boolean triggerBodyStarted = pending.matches("(?is).*\\bBEGIN\\b.*");
        boolean triggerBodyEnded = pending.matches("(?is).*\\bEND\\s*$");
        if (trigger && triggerBodyStarted && !triggerBodyEnded) {
          current.append(c);
        } else {
          addStatement(statements, current);
        }
      } else {
        current.append(c);
      }
    }
    if (singleQuoted || doubleQuoted || blockComment)
      throw new IllegalArgumentException("invalid schema SQL");
    addStatement(statements, current);
    if (statements.isEmpty()) throw new IllegalArgumentException("schema SQL is empty");
    return List.copyOf(statements);
  }

  private static void addStatement(List<String> statements, StringBuilder current) {
    String command = current.toString().trim();
    if (!command.isEmpty()) statements.add(command);
    current.setLength(0);
  }

  private static String readSql(String jdbcUrl, Path schemaFile) throws IOException {
    validateUrl(jdbcUrl);
    if (schemaFile == null || !Files.isRegularFile(schemaFile)) {
      throw new IllegalArgumentException("schema file is unavailable");
    }
    String sql = Files.readString(schemaFile, StandardCharsets.UTF_8);
    if (sql.isBlank()) throw new IllegalArgumentException("schema file is empty");
    return sql;
  }

  private static void validateUrl(String jdbcUrl) {
    SqlitePersistence.validateDatabaseUrl(jdbcUrl, false);
    if (jdbcUrl.contains("?mode=ro")) {
      throw new IllegalArgumentException(
          "schema initialization requires a new writable database URL");
    }
  }

  private static boolean hasUserObjects(Connection connection) throws SQLException {
    try (var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1")) {
      return rows.next();
    }
  }

  private static Target inspect(Connection connection) throws SQLException {
    String file;
    try (var statement = connection.createStatement();
        var rows = statement.executeQuery("PRAGMA database_list")) {
      if (!rows.next()) throw new SQLException("SQLite database file is unavailable");
      file = Path.of(rows.getString("file")).toAbsolutePath().normalize().toString();
    }
    if (file.isBlank()) throw new IllegalStateException("SQLite target must be a file");
    var relations = new ArrayList<String>();
    var otherObjects = new ArrayList<String>();
    try (var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name")) {
      while (rows.next()) {
        String name = rows.getString(1);
        if (name.startsWith("lexicon_")) relations.add(name);
        else otherObjects.add(name);
      }
    }
    return new Target(file, List.copyOf(relations), List.copyOf(otherObjects));
  }

  /**
   * 目标文件及其用户对象的只读检查结果。
   *
   * @param database 数据库连接返回的目标文件地址
   * @param lexiconRelations 当前词库命名空间内的对象名称
   * @param otherObjects 当前词库命名空间外的用户对象名称
   */
  public record Target(String database, List<String> lexiconRelations, List<String> otherObjects) {}
}
