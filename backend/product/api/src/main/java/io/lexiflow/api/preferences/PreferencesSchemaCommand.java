package io.lexiflow.api.preferences;

import io.lexiflow.api.deployment.StorageContract;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

/** 偏好数据库只接受发行物内的唯一当前结构；初始化与运行核对不会改写已存在的文件。 */
public final class PreferencesSchemaCommand {
  private static final String JDBC = "jdbc:sqlite:";

  private PreferencesSchemaCommand() {}

  /**
   * 显式创建最新版偏好库，或只读校验已存在的文件。
   *
   * @param args 含义：命令行参数。取值范围：恰好一个指向绝对路径的 jdbc:sqlite: URL，无查询参数。
   * @return 当前结构完整时为 0；不匹配时抛出异常。
   */
  public static int run(String[] args) {
    if (args.length != 1)
      throw new IllegalArgumentException("usage: --init-preferences <jdbc-url>");
    var path = databasePath(args[0]);
    if (Files.exists(path, LinkOption.NOFOLLOW_LINKS)) {
      requireCurrent(args[0]);
      System.out.println("PASS preferences database schema verified; left unchanged");
      return 0;
    }
    try {
      var schema = trustedSchema();
      Files.createDirectories(path.getParent());
      try {
        Files.createFile(
            path,
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
      } catch (UnsupportedOperationException ignored) {
        Files.createFile(path);
      }
      try (var connection = open(path, "rw")) {
        connection.setAutoCommit(false);
        install(connection, schema);
        try (var statement = connection.createStatement()) {
          statement.execute("PRAGMA application_id=" + StorageContract.preferenceApplicationId());
        }
        connection.commit();
      }
      System.out.println("PASS preferences database initialized");
      return 0;
    } catch (IOException | SQLException e) {
      throw new IllegalStateException("preferences database initialization failed", e);
    }
  }

  /**
   * 只读确认现有文件与可信最新版 SQL 的全部用户对象一致。
   *
   * @param jdbcUrl 含义：偏好数据库地址。取值范围：绝对、规范的专属 SQLite 文件地址，无查询参数。
   */
  public static void requireCurrent(String jdbcUrl) {
    var path = databasePath(jdbcUrl);
    if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
      throw new IllegalStateException(
          "preferences database is missing or unsafe; initialize it explicitly");
    try (var actual = open(path, "ro")) {
      if (!StorageContract.fingerprint(actual)
          .equals(StorageContract.expected(trustedSchema().getBytes(StandardCharsets.UTF_8))))
        throw new IllegalStateException(
            "preferences database schema mismatch; preserve the file and initialize a new target");
    } catch (IOException | SQLException e) {
      throw new IllegalStateException("preferences database schema verification failed", e);
    }
  }

  private static Path databasePath(String jdbcUrl) {
    if (!jdbcUrl.startsWith(JDBC) || jdbcUrl.indexOf('?') >= 0)
      throw new IllegalArgumentException(
          "preferences JDBC URL must target an absolute SQLite file");
    var path = Path.of(jdbcUrl.substring(JDBC.length()));
    if (!path.isAbsolute() || !path.equals(path.normalize()) || path.getParent() == null)
      throw new IllegalArgumentException(
          "preferences JDBC URL must target an absolute SQLite file");
    return path;
  }

  private static Connection open(Path path, String mode) throws SQLException {
    return DriverManager.getConnection(JDBC + path.toUri().toASCIIString() + "?mode=" + mode);
  }

  private static String trustedSchema() throws IOException {
    try (var input =
        PreferencesSchemaCommand.class.getResourceAsStream(
            "/META-INF/lexiflow-preferences-schema.sql")) {
      if (input == null) throw new IOException("trusted preferences schema unavailable");
      return new String(input.readAllBytes(), StandardCharsets.UTF_8);
    }
  }

  private static void install(Connection connection, String schema) throws SQLException {
    io.lexiflow.lexicon.platform.persistence.SqliteSchemaInitializer.executeTrustedSql(
        connection, schema.getBytes(StandardCharsets.UTF_8));
  }
}
