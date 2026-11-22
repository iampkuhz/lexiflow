package io.lexiflow.sqlite.platform;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.regex.Pattern;

/** Shared data-independent fingerprint of the actual SQLite catalogue. */
public final class SqliteSchemaShape {
  private SqliteSchemaShape() {}

  private static final Pattern TOKEN =
      Pattern.compile(
          "\\s+|--[^\\r\\n]*|/\\*[\\s\\S]*?\\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
              + "\u0060(?:\u0060\u0060|[^\u0060])*\u0060|\\[[^\\]]*\\]|[\\p{L}_][\\p{L}\\p{N}_$]*|"
              + "\\d+(?:\\.\\d+)?(?:[Ee][+-]?\\d+)?|->>|->|>=|<=|<>|!=|==|\\|\\||[^\\s]");

  /** 提取主库用户对象；不扫描业务行。 */
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
