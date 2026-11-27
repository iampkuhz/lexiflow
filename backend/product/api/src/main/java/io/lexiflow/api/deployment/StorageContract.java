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

  /**
   * 从可信 SQL 初始化临时内存库，并计算其 SQLite 结构摘要。 输入只用于构造内存结构，不读取或写入本机数据库文件。
   *
   * @param sql 含义：用于创建目标结构的可信 SQL 字节。取值范围：有效 UTF-8/SQLite SQL 脚本。
   * @return 初始化后主库目录的 SHA-256 十六进制摘要
   * @throws SQLException SQL 无效或结构读取失败时抛出
   */
  public static String expected(byte[] sql) throws SQLException {
    try (var db = DriverManager.getConnection("jdbc:sqlite::memory:")) {
      SqliteSchemaInitializer.executeTrustedSql(db, sql);
      return fingerprint(db);
    }
  }

  /**
   * 使用共享目录提取器计算连接所指数据库的结构摘要。 实际数据库与内存数据库采用相同的结构规范化规则。
   *
   * @param db 含义：待检查的 SQLite 连接。取值范围：已打开且可查询主库目录。
   * @return 主库结构的 SHA-256 十六进制摘要
   * @throws SQLException 目录读取失败时抛出
   */
  public static String fingerprint(Connection db) throws SQLException {
    return io.lexiflow.sqlite.platform.SqliteSchemaShape.fingerprint(db);
  }

  /**
   * 将两个数据库结构摘要与持久化身份语义绑定为一个摘要。 身份命名空间或偏好语义变化会改变最终摘要。
   *
   * @param lexicon 含义：词库结构摘要。取值范围：结构指纹文本。
   * @param preferences 含义：偏好库结构摘要。取值范围：结构指纹文本。
   * @return 组合存储合同的 SHA-256 十六进制摘要
   */
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

  /**
   * 返回偏好库专属的 SQLite application_id 标记。 该标记由当前持久语义命名空间确定，与词库发行和 HTTP 接口号独立。
   *
   * @return 可写入 SQLite application_id 的整数标记
   */
  public static int preferenceApplicationId() {
    return (int) Long.parseLong(sha(NAMESPACE + "\n" + PREFERENCES_SEMANTICS).substring(0, 7), 16);
  }

  /**
   * 以只读模式打开已存在的普通 SQLite 文件。 拒绝相对路径、非规范路径、符号链接和不存在的文件，不创建或迁移数据库。
   *
   * @param path 含义：待打开的数据库文件路径。取值范围：绝对、规范化且为普通文件。
   * @return 只读 SQLite 连接
   * @throws SQLException 文件不安全、缺失或驱动无法打开时抛出
   */
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

  /**
   * 开发期命令行入口，分别计算两份 SQL 的结构指纹并输出有界 JSON。 每个参数均是一个 SQL 文件路径；不修改输入文件。
   *
   * @param args 含义：词库 SQL 与偏好 SQL 路径。取值范围：恰好两个文件路径。
   * @throws Exception 文件读取或 SQL 结构计算失败时抛出
   */
  public static void main(String[] args) throws Exception {
    if (args.length != 2)
      throw new IllegalArgumentException("storage contract requires both SQL files");
    print(
        expected(Files.readAllBytes(Path.of(args[0]))),
        expected(Files.readAllBytes(Path.of(args[1]))));
  }

  /**
   * 从制品内打包的两份可信 SQL 计算结构指纹并核对嵌入身份。 身份与当前锁定摘要不一致时失败关闭。
   *
   * @throws Exception 资源缺失、结构计算失败或锁定身份不匹配时抛出
   */
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

  /**
   * 将一个实际数据库文件与对应的当前结构及身份命名空间核对。 只读打开文件；偏好库还必须证明持久化 ID 属于当前命名空间。
   *
   * @param kind 含义：数据库角色。取值范围：lexicon 或 preferences。
   * @param path 含义：待核对的数据库文件。取值范围：存在且安全的绝对普通文件路径。
   * @throws Exception 文件角色、文件结构、身份命名空间或读取过程无效时抛出
   */
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

  /**
   * 校验偏好库 application_id 与当前词条身份命名空间一致。 缺失或不匹配时拒绝将文件作为当前偏好库使用。
   *
   * @param db 含义：待核验的偏好 SQLite 连接。取值范围：已打开且可执行 PRAGMA。
   * @throws SQLException 标记缺失、读取失败或命名空间不匹配时抛出
   */
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
