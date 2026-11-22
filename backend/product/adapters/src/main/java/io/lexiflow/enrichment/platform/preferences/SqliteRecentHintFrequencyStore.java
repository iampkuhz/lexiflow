package io.lexiflow.enrichment.platform.preferences;

import io.lexiflow.enrichment.application.preferences.RecentHintFrequency;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 仅将每日词条次数写入已显式初始化的本机偏好数据库。 */
public final class SqliteRecentHintFrequencyStore implements RecentHintFrequency.Store {
  private final String jdbcUrl;

  /**
   * 配置现有本机偏好文件的访问方式，并禁止 SQLite 自动创建数据库。
   *
   * @param jdbcUrl 含义：已初始化的本机偏好 SQLite JDBC URL。取值范围：指向本机持久文件的 `jdbc:sqlite:` URL。
   */
  public SqliteRecentHintFrequencyStore(String jdbcUrl) {
    if (jdbcUrl == null || !jdbcUrl.startsWith("jdbc:sqlite:") || jdbcUrl.contains(":memory:"))
      throw new IllegalArgumentException("preferences JDBC URL must target a local SQLite file");
    var location = jdbcUrl.substring("jdbc:sqlite:".length());
    if (!location.startsWith("file:")) location = "file:" + location;
    this.jdbcUrl = "jdbc:sqlite:" + location + (location.contains("?") ? "&mode=rw" : "?mode=rw");
  }

  private java.sql.Connection connect() throws SQLException {
    var raw = jdbcUrl.substring("jdbc:sqlite:".length()).split("\\?", 2)[0];
    var path = java.nio.file.Path.of(raw.replaceFirst("^file:", ""));
    if (!java.nio.file.Files.isRegularFile(path) || java.nio.file.Files.isSymbolicLink(path))
      throw new SQLException("initialized preferences database is unavailable");
    var c = DriverManager.getConnection(jdbcUrl);
    try (var s = c.createStatement()) {
      s.execute("PRAGMA secure_delete=ON");
      s.execute("PRAGMA busy_timeout=5000");
      return c;
    } catch (SQLException failure) {
      try {
        c.close();
      } catch (SQLException suppressed) {
        failure.addSuppressed(suppressed);
      }
      throw failure;
    }
  }

  /**
   * 删除保留窗口之外的日期。
   *
   * <p>窗口范围为今日 D-7 至 D，包含两端。
   *
   * @param today 含义：后端本机时区的今日。取值范围：非 null 日期。
   */
  @Override
  public void prune(LocalDate today) {
    try (var c = connect();
        var s =
            c.prepareStatement("DELETE FROM recent_hint_daily_count WHERE day < ? OR day > ?")) {
      s.setString(1, today.minusDays(7).toString());
      s.setString(2, today.toString());
      s.executeUpdate();
    } catch (SQLException e) {
      throw failure(e);
    }
  }

  /**
   * 为提供的每个词条 ID 原子递增今日次数，重复 ID 也分别计数。
   *
   * @param today 含义：后端本机时区的今日。取值范围：非 null 日期。
   * @param ids 含义：实际呈现的稳定词条 ID。取值范围：每项为正 long，允许重复。
   */
  @Override
  public void record(LocalDate today, List<Long> ids) {
    try (var c = connect()) {
      c.setAutoCommit(false);
      try (var s =
          c.prepareStatement(
              "INSERT INTO recent_hint_daily_count(day,entry_id,count) VALUES(?,?,1) ON CONFLICT(day,entry_id) DO UPDATE SET count=count+1")) {
        for (long id : ids) {
          s.setString(1, today.toString());
          s.setLong(2, id);
          s.addBatch();
        }
        s.executeBatch();
        c.commit();
      } catch (SQLException e) {
        c.rollback();
        throw e;
      }
    } catch (SQLException e) {
      throw failure(e);
    }
  }

  /**
   * 汇总 D-6 至 D 的次数，并按次数降序、稳定 ID 顺序排列。
   *
   * @param today 含义：后端本机时区的今日。取值范围：非 null 日期。
   * @return 按次数降序、词条 ID 升序排列的每日总计。
   */
  @Override
  public Map<Long, Long> read(LocalDate today) {
    var counts = new LinkedHashMap<Long, Long>();
    try (var c = connect();
        var s =
            c.prepareStatement(
                "SELECT entry_id,SUM(count) FROM recent_hint_daily_count WHERE day BETWEEN ? AND ? GROUP BY entry_id ORDER BY SUM(count) DESC,entry_id")) {
      s.setString(1, today.minusDays(6).toString());
      s.setString(2, today.toString());
      try (var r = s.executeQuery()) {
        while (r.next()) counts.put(r.getLong(1), r.getLong(2));
      }
      return java.util.Collections.unmodifiableMap(counts);
    } catch (SQLException e) {
      throw failure(e);
    }
  }

  private static RuntimeException failure(SQLException e) {
    return new IllegalStateException("recent hint frequency storage unavailable", e);
  }
}
