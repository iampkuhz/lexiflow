package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.Objects;
import java.util.Set;

/** 在明确拥有的空 SQLite schema 上初始化，或精确核对幂等快照。 */
public final class SqliteDatasetPackage {
  private static final String BAD = "invalid package";
  static final Set<String> RELATIONS =
      Set.of(
          "lexicon_dataset",
          "lexicon_entry",
          "lexicon_form",
          "lexicon_lookup",
          "lexicon_form_entry_idx",
          "lexicon_entry_prewarm_hint_idx",
          "lexicon_entry_prewarm_block_idx",
          "lexicon_form_canonical_guard",
          "lexicon_form_canonical_update_guard",
          "lexicon_entry_canonical_update_guard");

  private SqliteDatasetPackage() {}

  /** 检查管理连接与显式目标文件身份，不创建文件或 schema。 */
  static void validateTarget(Connection connection, String expectedFile)
      throws SQLException, IOException {
    Objects.requireNonNull(connection);
    if (expectedFile == null
        || !Path.of(expectedFile).isAbsolute()
        || !Path.of(expectedFile).normalize().toString().equals(expectedFile)) throw invalid();
    try (var statement = connection.createStatement();
        var result = statement.executeQuery("PRAGMA database_list")) {
      if (!result.next() || !"main".equals(result.getString("name"))) throw invalid();
      String actual = Path.of(result.getString("file")).toRealPath().toString();
      if (!expectedFile.equals(actual)) throw invalid();
      while (result.next()) if (!"temp".equals(result.getString("name"))) throw invalid();
    }
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT 1 FROM sqlite_temp_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1")) {
      if (result.next()) throw invalid();
    }
  }

  /**
   * 在受控空 schema 中恢复或按完整包精确核对已有资料。
   *
   * @param connection 含义：显式 SQLite 管理连接。取值范围：归属匹配的目标库。
   * @param packagePath 含义：待恢复资料 ZIP。取值范围：普通文件且路径无符号链接。
   * @param expectedSha256 含义：外部可信发布摘要。取值范围：64 位小写十六进制。
   * @param expectedFile 含义：显式目标数据库文件。取值范围：绝对规范化路径并与连接一致。
   * @param trustedSql 含义：组合根内嵌最新 SQL。取值范围：由 API 可信资源读取的非空 SQL 字节数组。
   */
  public static void initialize(
      Connection connection,
      Path packagePath,
      String expectedSha256,
      String expectedFile,
      byte[] trustedSql)
      throws SQLException, IOException {
    try (var prepared = prepare(packagePath, expectedSha256, trustedSql)) {
      prepared.initialize(connection, expectedFile);
    }
  }

  /**
   * 在打开目标之前校验一次资料，供同一管理命令读取身份和初始化。
   *
   * @param packagePath 含义：待恢复资料 ZIP。取值范围：路径无符号链接的普通文件。
   * @param expectedSha256 含义：外部可信发布摘要。取值范围：64 位小写十六进制。
   * @param trustedSql 含义：组合根内嵌最新 SQL。取值范围：非空可信 SQL 字节。
   */
  public static Prepared prepare(Path packagePath, String expectedSha256, byte[] trustedSql)
      throws IOException {
    byte[] schema = trustedSql.clone();
    return new Prepared(
        DatasetPackageCodec.verifiedPackage(packagePath, expectedSha256, schema), schema);
  }

  /** 本次管理操作的私有已验证资料；调用方不能提供解包路径或替换可信结构。 */
  public static final class Prepared implements AutoCloseable {
    private final DatasetPackageCodec.Verified verified;
    private final byte[] trustedSql;
    private boolean closed;
    private boolean cleanupComplete;

    private Prepared(DatasetPackageCodec.Verified verified, byte[] trustedSql) {
      this.verified = verified;
      this.trustedSql = trustedSql;
    }

    /**
     * 返回本次验证的不可变资料清单；快照关闭后不能再读取。
     *
     * @return 与本次私有已验快照绑定的清单。
     */
    public DatasetPackageManifest manifest() {
      return openSnapshot().manifest();
    }

    /**
     * 在本方法管理的 SQLite 事务中初始化或核对幂等快照。
     *
     * @param connection 含义：管理连接。取值范围：连接到预期专属文件。
     * @param expectedFile 含义：预期数据库文件。取值范围：绝对规范路径。
     */
    public void initialize(Connection connection, String expectedFile)
        throws SQLException, IOException {
      initializeInternal(connection, openSnapshot(), expectedFile, trustedSql, true, () -> {});
    }

    /**
     * 在已有的事务中初始化；提交或回滚由调用者负责。
     *
     * @param connection 含义：管理连接。取值范围：已开启事务并连接到预期专属文件。
     * @param expectedFile 含义：预期数据库文件。取值范围：绝对规范路径。
     */
    public void initializeWithinTransaction(Connection connection, String expectedFile)
        throws SQLException, IOException {
      initializeInternal(connection, openSnapshot(), expectedFile, trustedSql, false, () -> {});
    }

    private DatasetPackageCodec.Verified openSnapshot() {
      if (closed) throw new IllegalStateException("verified package has been closed");
      return verified;
    }

    /**
     * 使快照失效并删除本次验证的私有解包文件。
     *
     * @throws IOException 无法删除私有文件。
     */
    @Override
    public void close() throws IOException {
      closed = true;
      if (cleanupComplete) return;
      verified.close();
      // 清理失败时仍不可复用，但允许外层失败清理重试剩余删除。
      cleanupComplete = true;
    }
  }

  /** 测试故障注入仅运行于资料写完而发布记录插入前。 */
  static void initialize(
      Connection connection,
      Path packagePath,
      String expectedSha256,
      String expectedFile,
      byte[] trustedSql,
      Runnable beforePublish)
      throws SQLException, IOException {
    try (var prepared = prepare(packagePath, expectedSha256, trustedSql)) {
      initializeInternal(
          connection,
          prepared.openSnapshot(),
          expectedFile,
          prepared.trustedSql,
          true,
          beforePublish);
    }
  }

  private static void initializeInternal(
      Connection connection,
      DatasetPackageCodec.Verified verified,
      String expectedFile,
      byte[] trustedSql,
      boolean manageTransaction,
      Runnable beforePublish)
      throws SQLException, IOException {
    validateTarget(connection, expectedFile);
    boolean auto = connection.getAutoCommit();
    if (!manageTransaction && auto)
      throw new IllegalStateException("caller-owned transaction is required");
    Throwable failure = null;
    try {
      if (manageTransaction) connection.setAutoCommit(false);
      var actual = relations(connection);
      if (actual.isEmpty()) {
        createSchema(connection, trustedSql);
        SqliteDatasetVerifier.verifyStructure(connection, trustedSql);
        load(connection, verified.file("entries.ndjson"), DatasetPackageTables.ENTRY);
        load(connection, verified.file("forms.ndjson"), DatasetPackageTables.FORM);
        beforePublish.run();
        load(connection, verified.file("dataset.ndjson"), DatasetPackageTables.DATASET);
        assertCounts(connection, verified.manifest());
        SqliteDatasetExport.validateProjection(connection);
      } else {
        if (!actual.equals(RELATIONS)) throw invalid();
        SqliteDatasetVerifier.verifyStructure(connection, trustedSql);
        assertCounts(connection, verified.manifest());
        compareExisting(connection, verified, trustedSql);
      }
      if (manageTransaction) connection.commit();
    } catch (SQLException | IOException | RuntimeException exception) {
      failure = exception;
      if (manageTransaction) rollbackPreservingFailure(connection, exception);
      throw exception;
    } finally {
      if (manageTransaction) restoreAutoCommit(connection, auto, failure);
    }
  }

  /** 引擎可能已自动回滚；清理失败只能附加，不能覆盖实际发布失败。 */
  private static void rollbackPreservingFailure(Connection connection, Throwable failure) {
    try {
      connection.rollback();
    } catch (SQLException cleanupFailure) {
      failure.addSuppressed(cleanupFailure);
    }
  }

  /** 成功路径必须恢复连接状态；失败路径保留首错并附加状态恢复错误。 */
  private static void restoreAutoCommit(Connection connection, boolean auto, Throwable failure)
      throws SQLException {
    try {
      connection.setAutoCommit(auto);
    } catch (SQLException cleanupFailure) {
      if (failure == null) throw cleanupFailure;
      failure.addSuppressed(cleanupFailure);
    }
  }

  static Set<String> relations(Connection connection) throws SQLException {
    var found = new java.util.HashSet<String>();
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' "
                    + "AND type IN ('table','view','index','trigger')")) {
      while (result.next()) found.add(result.getString(1));
    }
    return found;
  }

  static void assertShape(Connection connection) throws SQLException, IOException {
    for (var table : DatasetPackageTables.TABLES) {
      try (var statement = connection.createStatement();
          var rows = statement.executeQuery("PRAGMA table_info('" + table.name() + "')")) {
        int index = 0;
        while (rows.next()) {
          if (index >= table.columns().size()) throw invalid();
          String column = table.columns().get(index++);
          if (!column.equals(rows.getString("name"))
              || !DatasetPackageTables.TYPES.get(column).equalsIgnoreCase(rows.getString("type")))
            throw invalid();
          boolean nullable = table == DatasetPackageTables.ENTRY && column.equals("gloss");
          boolean integerPrimaryKey =
              (table == DatasetPackageTables.DATASET && column.equals("dataset_id"))
                  || (table == DatasetPackageTables.ENTRY && column.equals("entry_id"));
          if (rows.getInt("notnull") != (nullable || integerPrimaryKey ? 0 : 1)) throw invalid();
          int expectedKey =
              table == DatasetPackageTables.DATASET && column.equals("dataset_id")
                      || table == DatasetPackageTables.ENTRY && column.equals("entry_id")
                      || table == DatasetPackageTables.FORM && column.equals("normalized_form")
                  ? 1
                  : table == DatasetPackageTables.FORM && column.equals("entry_id") ? 2 : 0;
          if (rows.getInt("pk") != expectedKey) throw invalid();
        }
        if (index != table.columns().size()) throw invalid();
      }
    }
    try (var statement = connection.createStatement();
        var rows = statement.executeQuery("PRAGMA table_list")) {
      var tables = new java.util.HashMap<String, int[]>();
      while (rows.next()) {
        String name = rows.getString("name");
        if (DatasetPackageTables.TABLES.stream().anyMatch(table -> table.name().equals(name)))
          tables.put(name, new int[] {rows.getInt("wr"), rows.getInt("strict")});
      }
      if (tables.size() != 3 || tables.values().stream().anyMatch(flags -> flags[1] != 1))
        throw invalid();
      if (tables.get("lexicon_form")[0] != 1
          || tables.get("lexicon_entry")[0] != 0
          || tables.get("lexicon_dataset")[0] != 0) throw invalid();
    }
    if (!relations(connection).equals(RELATIONS)) throw invalid();
  }

  /** 对 SQLite catalog 中的全部受控结构定义求确定性摘要。 */
  static String shapeFingerprint(Connection connection) throws SQLException {
    var text = new StringBuilder();
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT type,name,coalesce(sql,'') FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")) {
      while (result.next()) {
        text.append(result.getString(1))
            .append('\u001f')
            .append(result.getString(2))
            .append('\u001f')
            .append(result.getString(3))
            .append('\n');
      }
    }
    return DatasetPackageCodec.sha256(text.toString().getBytes(StandardCharsets.UTF_8));
  }

  private static void createSchema(Connection connection, byte[] sqlBytes)
      throws SQLException, IOException {
    SqliteSchemaInitializer.executeTrustedSql(connection, sqlBytes);
    if (!relations(connection).equals(RELATIONS)) throw invalid();
  }

  private static void load(Connection connection, Path path, DatasetPackageTables.Table table)
      throws IOException, SQLException {
    String names = String.join(",", table.columns());
    String placeholders =
        String.join(",", java.util.Collections.nCopies(table.columns().size(), "?"));
    String sql = "INSERT INTO " + table.name() + " (" + names + ") VALUES (" + placeholders + ")";
    try (var insert = connection.prepareStatement(sql);
        var lines = new DatasetPackageCodec.BoundedLines(path)) {
      int batch = 0;
      String line;
      while ((line = lines.next()) != null) {
        var row = DatasetPackageCodec.validateRow(DatasetPackageCodec.parseJson(line), table);
        for (int i = 0; i < table.columns().size(); i++) {
          String column = table.columns().get(i);
          bind(
              connection,
              insert,
              i + 1,
              column,
              DatasetPackageTables.TYPES.get(column),
              row.get(column));
        }
        insert.addBatch();
        if (++batch == 500) {
          insert.executeBatch();
          batch = 0;
        }
      }
      if (batch != 0) insert.executeBatch();
    }
  }

  private static void bind(
      Connection connection,
      PreparedStatement insert,
      int index,
      String column,
      String type,
      Object value)
      throws SQLException, IOException {
    if (value == null) {
      insert.setNull(
          index, type.equals("INTEGER") ? java.sql.Types.INTEGER : java.sql.Types.VARCHAR);
      return;
    }
    switch (type) {
      case "INTEGER" -> insert.setLong(index, DatasetPackageCodec.integer(value, 0));
      case "TEXT" -> {
        if (Set.of("source_manifest", "generation_config", "generation_inputs").contains(column)) {
          // 结构化文本按规范序列化编码；普通标量文本保留原值。
          insert.setString(
              index, new String(DatasetPackageCodec.jsonBytes(value), StandardCharsets.UTF_8));
        } else insert.setString(index, (String) value);
      }
      default -> throw invalid();
    }
  }

  private static void assertCounts(Connection connection, DatasetPackageManifest manifest)
      throws SQLException, IOException {
    for (int i = 0; i < DatasetPackageTables.TABLES.size(); i++) {
      var table = DatasetPackageTables.TABLES.get(i);
      try (var statement = connection.createStatement();
          var result = statement.executeQuery("SELECT COUNT(*) FROM " + table.name())) {
        if (!result.next() || result.getLong(1) != manifest.files().get(i).rows()) throw invalid();
      }
    }
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT lexicon_version, preparation_policy, entry_count, lookup_count, generation_version, generation_code_version, generation_source_digest, content_sha256, generation_inputs, generation_config "
                    + "FROM lexicon_dataset WHERE dataset_id=1")) {
      if (!result.next()
          || result.getLong(1) != manifest.datasetVersion()
          || !result.getString(2).equals(manifest.preparationPolicy())
          || result.getLong(3) != manifest.files().get(1).rows()
          || result.getLong(4) != count(connection, "lexicon_lookup")
          || !result.getString(5).trim().equals(manifest.generationVersion())
          || !result.getString(6).trim().equals(manifest.generationCodeVersion())
          || !result.getString(7).trim().equals(manifest.generationSourceDigest())
          || !result.getString(8).equals(manifest.contentSha256())
          || !jsonEquals(result.getString(9), manifest.generationInputsJson())
          || !jsonEquals(result.getString(10), manifest.generationConfigJson())
          || result.next()) throw invalid();
    }
    if (!DatasetContentChecksum.calculate(connection).equals(manifest.contentSha256()))
      throw invalid();
  }

  private static long count(Connection connection, String relation) throws SQLException {
    try (var statement = connection.createStatement();
        var result = statement.executeQuery("SELECT COUNT(*) FROM " + relation)) {
      if (!result.next()) throw new SQLException("count query returned no row");
      return result.getLong(1);
    }
  }

  private static boolean jsonEquals(String left, String right) {
    try {
      return java.util.Arrays.equals(
          DatasetPackageCodec.jsonBytes(DatasetPackageCodec.parseJson(left)),
          DatasetPackageCodec.jsonBytes(DatasetPackageCodec.parseJson(right)));
    } catch (IOException failure) {
      return false;
    }
  }

  private static void compareExisting(
      Connection connection, DatasetPackageCodec.Verified verified, byte[] sql)
      throws SQLException, IOException {
    Path output = Files.createTempFile("lexiflow-existing-", ".zip").toRealPath();
    Files.delete(output);
    try {
      SqliteDatasetExport.export(connection, output, verified.file("approvals.json"), sql, false);
      if (!DatasetPackageCodec.sha256(output).equals(verified.sha256())) throw invalid();
    } finally {
      Files.deleteIfExists(output);
    }
  }

  private static IOException invalid() {
    return new IOException(BAD);
  }
}
