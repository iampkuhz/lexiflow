package io.lexiflow.sqlite.platform;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.regex.Pattern;

/** 计算 SQLite 实际目录的结构指纹，不读取业务行数据。 结果只依赖数据库目录定义，并由结构化词法单元确定。 */
public final class SqliteSchemaShape {
  private SqliteSchemaShape() {}

  private static final Pattern TOKEN =
      Pattern.compile(
          "\\s+|--[^\\r\\n]*|/\\*[\\s\\S]*?\\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
              + "\u0060(?:\u0060\u0060|[^\u0060])*\u0060|\\[[^\\]]*\\]|[\\p{L}_][\\p{L}\\p{N}_$]*|"
              + "\\d+(?:\\.\\d+)?(?:[Ee][+-]?\\d+)?|->>|->|>=|<=|<>|!=|==|\\|\\||[^\\s]");

  /**
   * 提取主库用户对象的结构指纹；不扫描业务行。 忽略 SQLite 内部对象，并对 DDL 词法单元作确定性规范化。
   *
   * @param db 含义：待检查的 SQLite 连接。取值范围：已打开且可查询 main.sqlite_schema。
   * @return 主库结构的 SHA-256 十六进制摘要
   * @throws SQLException 目录读取或对象定义缺失时抛出
   */
  public static String fingerprint(Connection db) throws SQLException {
    var text = new StringBuilder("lexiflow-sqlite-structure-1\n");
    try (var statement = db.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT type,name,tbl_name,sql FROM main.sqlite_schema"
                    + " WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name")) {
      while (rows.next()) {
        for (int i = 1; i <= 3; i++) field(text, rows.getString(i));
        String ddl = rows.getString(4);
        if (ddl == null) throw new SQLException("missing SQLite object definition");
        var tokens = TOKEN.matcher(ddl);
        while (tokens.find()) {
          var token = tokens.group();
          if (token.isBlank() || token.startsWith("--") || token.startsWith("/*")) continue;
          if (Character.isLetter(token.charAt(0))) token = token.toLowerCase(Locale.ROOT);
          field(text, token);
        }
        text.append('\n');
      }
    }
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256")
                  .digest(text.toString().getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private static void field(StringBuilder out, String value) {
    out.append(value.length()).append(':').append(value);
  }
}
