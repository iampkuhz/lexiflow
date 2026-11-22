package io.lexiflow.enrichment.platform.preferences;

import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.ArrayList;

/** 将显式选择的本机偏好持久化到独立 SQLite 数据库。 该适配器只读写偏好表，不创建数据库或记录观看、字幕数据。 */
public final class SqlitePreferencesStore implements LocalPreferences.Store {
  private final String jdbcUrl;

  /**
   * 为已显式初始化的 SQLite 文件配置存储适配器。 调用方负责提供专属数据库的 JDBC 地址；本构造器不会创建文件。
   *
   * @param jdbcUrl 含义：偏好数据库的 JDBC 地址。取值范围：指向已初始化本机 SQLite 文件的 jdbc:sqlite: URL。
   */
  public SqlitePreferencesStore(String jdbcUrl) {
    this.jdbcUrl = jdbcUrl;
  }

  private java.sql.Connection connect() throws SQLException {
    return DriverManager.getConnection(jdbcUrl);
  }

  /**
   * 读取持久化的阈值与隐藏词条快照，数据库不可用时抛出存储异常。
   *
   * @return 当前本机偏好快照；尚未设置档位时返回默认档位。
   */
  @Override
  public LocalPreferences.State read() {
    try (var c = connect();
        var q = c.createStatement()) {
      int cutoff = 1500;
      try (var r =
          q.executeQuery(
              "SELECT preference_value FROM local_preference WHERE"
                  + " preference_key='commonWordCutoff'")) {
        if (r.next()) cutoff = Integer.parseInt(r.getString(1));
      }
      var hidden = new ArrayList<LocalPreferences.Hidden>();
      try (var r =
          q.executeQuery(
              "SELECT entry_id, lemma, gloss FROM hidden_lexicon_entry ORDER BY lemma, entry_id")) {
        while (r.next())
          hidden.add(new LocalPreferences.Hidden(r.getString(1), r.getString(2), r.getString(3)));
      }
      return new LocalPreferences.State(cutoff, hidden);
    } catch (SQLException e) {
      throw failure(e);
    }
  }

  /**
   * 写入受支持的常用词阈值并返回更新后的完整快照。
   *
   * @param cutoff 含义：常用词排名阈值。取值范围：1000、1500 或 2000。
   */
  @Override
  public LocalPreferences.State setCutoff(int cutoff) {
    return write(
        "INSERT INTO local_preference(preference_key,preference_value) VALUES('commonWordCutoff',?)"
            + " ON CONFLICT(preference_key) DO UPDATE SET"
            + " preference_value=excluded.preference_value",
        s -> s.setString(1, Integer.toString(cutoff)));
  }

  /**
   * 按稳定词条身份幂等写入本机隐藏偏好。
   *
   * @param id 含义：稳定词条身份。取值范围：规范化词元对应的正十进制 long 字符串。
   * @param lemma 含义：规范化词元。取值范围：非空且长度不超过 128 个字符。
   * @param gloss 含义：展示释义。取值范围：非空且长度不超过 500 个字符。
   */
  @Override
  public LocalPreferences.State hide(String id, String lemma, String gloss) {
    return write(
        "INSERT INTO hidden_lexicon_entry(entry_id,lemma,gloss) VALUES(?,?,?) ON CONFLICT(entry_id)"
            + " DO UPDATE SET lemma=excluded.lemma, gloss=excluded.gloss",
        s -> {
          s.setString(1, id);
          s.setString(2, lemma);
          s.setString(3, gloss);
        });
  }

  /**
   * 删除指定身份的隐藏偏好；目标不存在时保持原状态。
   *
   * @param id 含义：需恢复的稳定词条身份。取值范围：正十进制 long 字符串。
   */
  @Override
  public LocalPreferences.State restore(String id) {
    return write("DELETE FROM hidden_lexicon_entry WHERE entry_id=?", s -> s.setString(1, id));
  }

  private LocalPreferences.State write(String sql, Binder binder) {
    try (var c = connect();
        var s = c.prepareStatement(sql)) {
      binder.bind(s);
      s.executeUpdate();
      return read();
    } catch (SQLException e) {
      throw failure(e);
    }
  }

  private static RuntimeException failure(SQLException e) {
    return new IllegalStateException("local preferences storage unavailable", e);
  }

  /** 为一条预编译 SQLite 语句绑定参数，避免拼接用户输入。 */
  @FunctionalInterface
  private interface Binder {
    void bind(java.sql.PreparedStatement s) throws SQLException;
  }
}
