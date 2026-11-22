package io.lexiflow.api.deployment;

import io.lexiflow.api.runtime.ContractIdentity;
import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import io.lexiflow.lexicon.platform.persistence.SqliteSchemaInitializer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.HexFormat;

/** SQLite 的确定性结构身份；构建与实际卷共用同一个计算器。 */
public final class StorageContract {
  private static final String NAMESPACE =
      "canonical-lemma:"
          + LexiconIdentity.entryId("constructor")
          + ":"
          + LexiconIdentity.entryId(" Two  Words ");
  private static final String PREFERENCES_SEMANTICS =
      "local-ISO-calendar-date:actual-render-count:canonical-lemma-entry-id";

  private StorageContract() {}

  /** 从可信 SQL 初始化一个临时内存库，只提取结构。 */
  public static String expected(byte[] sql) throws SQLException {
    try (var db = DriverManager.getConnection("jdbc:sqlite::memory:")) {
      SqliteSchemaInitializer.executeTrustedSql(db, sql);
      return fingerprint(db);
    }
  }

  /** Reuse the same catalogue extractor for actual and in-memory databases. */
  public static String fingerprint(Connection db) throws SQLException {
    return io.lexiflow.sqlite.platform.SqliteSchemaShape.fingerprint(db);
  }

  /** 将 DDL 与持久语义绑定；直接改变 ID 运算的回归样例会改变词条空间身份。 */
  public static String combined(String lexicon, String preferences) {
    return sha(
        "lexiflow-storage-1\n"
            + lexicon
            + "\n"
            + preferences
            + "\n"
            + NAMESPACE
            + "\n"
            + PREFERENCES_SEMANTICS
            + "\n");
  }

  /** 偏好库的标识空间标记，独立于词库发行和应用接口。 */
  public static int preferenceApplicationId() {
    return (int) Long.parseLong(sha(NAMESPACE + "\n" + PREFERENCES_SEMANTICS).substring(0, 7), 16);
  }

  /** 仅打开已存在的普通文件，不创建、迁移或清理数据库。 */
  public static Connection openReadOnly(Path path) throws SQLException {
    if (!path.isAbsolute()
        || !path.equals(path.normalize())
        || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
      throw new SQLException("SQLite file missing or unsafe");
    return DriverManager.getConnection("jdbc:sqlite:" + path.toUri().toASCIIString() + "?mode=ro");
  }

  private static String sha(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  /** 开发工具只消费一个有界 JSON 结果。 */
  public static void main(String[] args) throws Exception {
    if (args.length != 2)
      throw new IllegalArgumentException("storage contract requires both SQL files");
    print(
        expected(Files.readAllBytes(Path.of(args[0]))),
        expected(Files.readAllBytes(Path.of(args[1]))));
  }

  /** 从实际打包的可信 SQL 计算独立期望值。 */
  public static void bundled() throws Exception {
    try (var lexicon = StorageContract.class.getResourceAsStream("/META-INF/lexiflow-schema.sql");
        var preferences =
            StorageContract.class.getResourceAsStream(
                "/META-INF/lexiflow-preferences-schema.sql")) {
      if (lexicon == null || preferences == null)
        throw new IllegalStateException("missing SQLite schemas");
      var a = expected(lexicon.readAllBytes());
      var b = expected(preferences.readAllBytes());
      if (!combined(a, b).equals(ContractIdentity.storageDigest()))
        throw new IllegalStateException("bundled SQLite contract differs from lock");
      print(a, b);
    }
  }

  /** Compare one actual file with the corresponding current schema and identity namespace. */
  public static void verify(String kind, Path path) throws Exception {
    var preferences = "preferences".equals(kind);
    if (!preferences && !"lexicon".equals(kind))
      throw new IllegalArgumentException("unknown SQLite role");
    var schema =
        preferences ? "/META-INF/lexiflow-preferences-schema.sql" : "/META-INF/lexiflow-schema.sql";
    try (var input = StorageContract.class.getResourceAsStream(schema);
        var actual = openReadOnly(path)) {
      if (input == null || !fingerprint(actual).equals(expected(input.readAllBytes())))
        throw new IllegalStateException(kind + " database schema mismatch");
      if (preferences) requirePreferenceNamespace(actual);
    }
  }

  /** Fails closed if the file does not prove its persisted IDs belong to the current namespace. */
  public static void requirePreferenceNamespace(Connection db) throws SQLException {
    try (var statement = db.createStatement();
        var rows = statement.executeQuery("PRAGMA application_id")) {
      if (!rows.next() || rows.getInt(1) != preferenceApplicationId())
        throw new SQLException("preferences entry-id namespace unverified");
    }
  }

  private static void print(String lexicon, String preferences) {
    System.out.printf(
        "{\"lexicon\":\"%s\",\"preferences\":\"%s\",\"sha256\":\"%s\"}%n",
        lexicon, preferences, combined(lexicon, preferences));
  }
}
