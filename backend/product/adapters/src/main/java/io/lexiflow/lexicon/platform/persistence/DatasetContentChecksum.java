package io.lexiflow.lexicon.platform.persistence;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.HexFormat;

/** 对已发布词条与词形的确定性内容摘要；不包含导入时间或摘要字段自身。 */
public final class DatasetContentChecksum {
  private DatasetContentChecksum() {}

  /**
   * 按冻结排序和字段顺序计算词库内容摘要。
   *
   * @param connection 含义：当前词库 schema 的连接。取值范围：有效 PostgreSQL 事务连接。
   */
  public static String calculate(Connection connection) throws SQLException {
    var json = new StringBuilder("{\"entries\":[");
    boolean[] first = {true};
    try (var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT entry_id,lemma,gloss,ranked_word,hint_priority,complex_list_count,cache_priority FROM lexicon_entry ORDER BY entry_id")) {
      while (rows.next()) {
        if (!first[0]) json.append(',');
        first[0] = false;
        json.append('[').append(rows.getLong(1)).append(',');
        string(json, rows.getString(2));
        json.append(',');
        string(json, rows.getString(3));
        json.append(',')
            .append(rows.getBoolean(4))
            .append(',')
            .append(rows.getInt(5))
            .append(',')
            .append(rows.getInt(6))
            .append(',')
            .append(rows.getInt(7))
            .append(']');
      }
    }
    json.append("],\"forms\":[");
    first[0] = true;
    try (var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT normalized_form,entry_id FROM lexicon_form ORDER BY normalized_form COLLATE \"C\",entry_id")) {
      while (rows.next()) {
        if (!first[0]) json.append(',');
        first[0] = false;
        json.append('[');
        string(json, rows.getString(1));
        json.append(',').append(rows.getLong(2)).append(']');
      }
    }
    json.append("]}");
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256")
                  .digest(json.toString().getBytes(StandardCharsets.UTF_8)));
    } catch (java.security.NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private static void string(StringBuilder out, String value) {
    if (value == null) {
      out.append("null");
      return;
    }
    out.append('"');
    for (int i = 0; i < value.length(); i++) {
      char c = value.charAt(i);
      switch (c) {
        case '"' -> out.append("\\\"");
        case '\\' -> out.append("\\\\");
        case '\b' -> out.append("\\b");
        case '\f' -> out.append("\\f");
        case '\n' -> out.append("\\n");
        case '\r' -> out.append("\\r");
        case '\t' -> out.append("\\t");
        default -> {
          if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
          else out.append(c);
        }
      }
    }
    out.append('"');
  }
}
