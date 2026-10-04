package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Types;
import java.time.OffsetDateTime;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/** 在明确拥有的空 PostgreSQL schema 上初始化，或精确核对幂等快照。 */
public final class PostgresDatasetPackage {
  private static final String BAD = "invalid package";
  private static final Set<String> RELATIONS =
      Set.of(
          "lexicon_dataset",
          "lexicon_dataset_pkey",
          "lexicon_entry",
          "lexicon_entry_pkey",
          "lexicon_entry_lemma_key",
          "lexicon_form",
          "lexicon_form_pk",
          "lexicon_form_entry_idx",
          "lexicon_entry_prewarm_hint_idx",
          "lexicon_entry_prewarm_block_idx");

  private PostgresDatasetPackage() {}

  /** 检查管理连接、显式目标与实际 schema owner，不创建 schema。 */
  static void validateTarget(Connection connection, String expectedDatabase, String expectedSchema)
      throws SQLException, IOException {
    Objects.requireNonNull(connection);
    if (expectedDatabase == null
        || expectedSchema == null
        || !expectedSchema.matches("lexiflow_[a-zA-Z0-9_]+")) throw invalid();
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery("SELECT current_database(), current_schema(), current_user")) {
      if (!result.next()
          || !expectedDatabase.equals(result.getString(1))
          || !expectedSchema.equals(result.getString(2))) throw invalid();
      String user = result.getString(3);
      try (var owner =
          connection.prepareStatement(
              "SELECT pg_get_userbyid(n.nspowner) FROM pg_namespace n WHERE n.nspname = ?")) {
        owner.setString(1, expectedSchema);
        try (var owners = owner.executeQuery()) {
          if (!owners.next() || !user.equals(owners.getString(1))) throw invalid();
        }
      }
    }
  }

  /**
   * 在受控空 schema 中恢复或按完整包精确核对已有资料。
   *
   * @param connection 含义：显式 PostgreSQL 管理连接。取值范围：归属匹配的目标库。
   * @param packagePath 含义：待恢复资料 ZIP。取值范围：普通文件且路径无符号链接。
   * @param expectedSha256 含义：外部可信发布摘要。取值范围：64 位小写十六进制。
   * @param expectedDatabase 含义：显式数据库身份。取值范围：与当前连接一致。
   * @param expectedSchema 含义：显式受控 schema 身份。取值范围：归属当前用户的 lexiflow_ 前缀。
   * @param trustedSql 含义：组合根内嵌最新 SQL。取值范围：由 API 可信资源读取的非空 SQL 字节数组。
   */
  public static void initialize(
      Connection connection,
      Path packagePath,
      String expectedSha256,
      String expectedDatabase,
      String expectedSchema,
      byte[] trustedSql)
      throws SQLException, IOException {
    initialize(
        connection,
        packagePath,
        expectedSha256,
        expectedDatabase,
        expectedSchema,
        trustedSql,
        () -> {});
  }

  /** 测试 callback 仅允许在资料写完而 dataset 尚未发布时注入故障。 */
  static void initialize(
      Connection connection,
      Path packagePath,
      String expectedSha256,
      String expectedDatabase,
      String expectedSchema,
      byte[] trustedSql,
      Runnable beforePublish)
      throws SQLException, IOException {
    try (var verified =
        DatasetPackageCodec.verifiedPackage(packagePath, expectedSha256, trustedSql)) {
      validateTarget(connection, expectedDatabase, expectedSchema);
      boolean auto = connection.getAutoCommit();
      int isolation = connection.getTransactionIsolation();
      try {
        connection.setAutoCommit(false);
        connection.setTransactionIsolation(Connection.TRANSACTION_READ_COMMITTED);
        try (var lock =
            connection.prepareStatement(
                "SELECT pg_advisory_xact_lock(hashtext(current_database()), hashtext(current_schema()))")) {
          lock.execute();
        }
        var actual = relations(connection);
        if (actual.isEmpty()) {
          createSchema(connection, trustedSql);
          load(connection, verified.file("entries.ndjson"), DatasetPackageTables.ENTRY);
          load(connection, verified.file("forms.ndjson"), DatasetPackageTables.FORM);
          beforePublish.run();
          load(connection, verified.file("dataset.ndjson"), DatasetPackageTables.DATASET);
          assertCounts(connection, verified.manifest());
          PostgresDatasetExport.validateProjection(connection);
        } else {
          if (!actual.equals(RELATIONS)) throw invalid();
          // SHARE 阻断普通 DML 与 DDL，锁等待完成后才重新检查关系和全部内容。
          try (var tables = connection.createStatement()) {
            tables.execute("LOCK TABLE lexicon_dataset, lexicon_entry, lexicon_form IN SHARE MODE");
          }
          if (!relations(connection).equals(RELATIONS)) throw invalid();
          assertShape(connection);
          assertCounts(connection, verified.manifest());
          compareExisting(connection, verified, trustedSql);
        }
        connection.commit();
      } catch (SQLException | IOException | RuntimeException exception) {
        connection.rollback();
        throw exception;
      } finally {
        connection.setTransactionIsolation(isolation);
        connection.setAutoCommit(auto);
      }
    }
  }

  private static Set<String> relations(Connection connection) throws SQLException {
    var found = new java.util.HashSet<String>();
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    + "WHERE n.nspname=current_schema() AND c.relkind IN ('r','p','v','m','S','f','i','I')")) {
      while (result.next()) found.add(result.getString(1));
    }
    return found;
  }

  private static void assertShape(Connection connection) throws SQLException, IOException {
    for (var table : DatasetPackageTables.TABLES) {
      try (var statement =
          connection.prepareStatement(
              "SELECT column_name,data_type,is_nullable FROM information_schema.columns "
                  + "WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position")) {
        statement.setString(1, table.name());
        try (var rows = statement.executeQuery()) {
          int index = 0;
          while (rows.next()) {
            if (index >= table.columns().size()) throw invalid();
            String column = table.columns().get(index++);
            String expectedType = DatasetPackageTables.TYPES.get(column);
            if (!column.equals(rows.getString(1))
                || !sqlType(expectedType).equals(rows.getString(2))) {
              throw invalid();
            }
            boolean nullable = table == DatasetPackageTables.ENTRY && column.equals("gloss");
            if (!rows.getString(3).equals(nullable ? "YES" : "NO")) throw invalid();
          }
          if (index != table.columns().size()) throw invalid();
        }
      }
    }
    var requiredConstraints =
        Set.of(
            "lexicon_dataset_pkey",
            "lexicon_dataset_singleton_ck",
            "lexicon_dataset_version_ck",
            "lexicon_dataset_manifest_ck",
            "lexicon_dataset_source_count_ck",
            "lexicon_dataset_entry_count_ck",
            "lexicon_dataset_lookup_count_ck",
            "lexicon_dataset_source_rows_ck",
            "lexicon_dataset_policy_ck",
            "lexicon_entry_pkey",
            "lexicon_entry_positive_id_ck",
            "lexicon_entry_lemma_key",
            "lexicon_entry_lemma_ck",
            "lexicon_entry_gloss_ck",
            "lexicon_entry_hint_priority_ck",
            "lexicon_entry_complex_list_count_ck",
            "lexicon_entry_cache_priority_ck",
            "lexicon_form_pk",
            "lexicon_form_window_ck",
            "lexicon_form_entry_fk");
    var actualConstraints = new java.util.HashSet<String>();
    try (var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                    + "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema()")) {
      while (rows.next()) actualConstraints.add(rows.getString(1));
    }
    if (!actualConstraints.equals(requiredConstraints)) throw invalid();
    assertIndex(connection, "lexicon_form_pk", "(normalized_form, entry_id)", null);
    assertIndex(connection, "lexicon_entry_lemma_key", "(lemma)", null);
    assertIndex(connection, "lexicon_form_entry_idx", "(entry_id)", null);
    assertIndex(
        connection,
        "lexicon_entry_prewarm_hint_idx",
        "(cache_priority DESC, entry_id)",
        "gloss IS NOT NULL AND cache_priority > 0");
    assertIndex(
        connection,
        "lexicon_entry_prewarm_block_idx",
        "(cache_priority DESC, entry_id)",
        "gloss IS NULL AND cache_priority > 0");
  }

  private static String sqlType(String type) {
    return switch (type) {
      case "timestamptz" -> "timestamp with time zone";
      default -> type;
    };
  }

  private static void assertIndex(
      Connection connection, String name, String columns, String predicate)
      throws SQLException, IOException {
    try (var statement =
        connection.prepareStatement(
            "SELECT pg_get_indexdef(i.oid),pg_get_expr(x.indpred,x.indrelid) "
                + "FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid "
                + "JOIN pg_namespace n ON n.oid=i.relnamespace "
                + "WHERE n.nspname=current_schema() AND i.relname=?")) {
      statement.setString(1, name);
      try (var rows = statement.executeQuery()) {
        if (!rows.next() || !rows.getString(1).contains(columns)) throw invalid();
        String actualPredicate = rows.getString(2);
        if (predicate == null
            ? actualPredicate != null
            : actualPredicate == null
                || !actualPredicate
                    .replaceAll("[()\\s]", "")
                    .toLowerCase(Locale.ROOT)
                    .contains(predicate.replaceAll("[()\\s]", "").toLowerCase(Locale.ROOT))) {
          throw invalid();
        }
        if (rows.next()) throw invalid();
      }
    }
  }

  /** 对列类型/null/default、约束表达式和索引定义求确定性 catalog 摘要。 */
  static String shapeFingerprint(Connection connection) throws SQLException {
    String sql =
        "SELECT kind, owner, name, definition, extra FROM ("
            + "SELECT 'C' AS kind,c.relname AS owner,a.attname AS name,"
            + "format_type(a.atttypid,a.atttypmod) AS definition,"
            + "(a.attnotnull::text || ':' || coalesce(pg_get_expr(d.adbin,d.adrelid),'')) AS extra "
            + "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            + "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
            + "LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
            + "WHERE n.nspname=current_schema() AND c.relkind='r' "
            + "UNION ALL SELECT 'K',r.relname,k.conname,pg_get_constraintdef(k.oid,true),'' "
            + "FROM pg_constraint k JOIN pg_class r ON r.oid=k.conrelid "
            + "JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() "
            + "UNION ALL SELECT 'I',t.relname,i.relname,pg_get_indexdef(i.oid),'' "
            + "FROM pg_class i JOIN pg_index x ON x.indexrelid=i.oid "
            + "JOIN pg_class t ON t.oid=x.indrelid "
            + "JOIN pg_namespace n ON n.oid=i.relnamespace "
            + "WHERE n.nspname=current_schema()) facts ORDER BY kind,owner,name";
    String schema = connection.getSchema();
    var text = new StringBuilder();
    try (var statement = connection.createStatement();
        var result = statement.executeQuery(sql)) {
      while (result.next()) {
        for (int i = 1; i <= 5; i++) {
          String value = result.getString(i);
          if (i == 4 && "I".equals(result.getString(1)))
            value = value.replace("\"" + schema + "\".", "").replace(schema + ".", "");
          text.append(value).append('\u001f');
        }
        text.append('\n');
      }
    }
    return DatasetPackageCodec.sha256(text.toString().getBytes(StandardCharsets.UTF_8));
  }

  private static void createSchema(Connection connection, byte[] sqlBytes)
      throws SQLException, IOException {
    String sql = new String(sqlBytes, StandardCharsets.UTF_8);
    for (String command : sql.split(";")) {
      if (!command.isBlank())
        try (var statement = connection.createStatement()) {
          statement.execute(command);
        }
    }
    if (!relations(connection).equals(RELATIONS)) throw invalid();
  }

  private static void load(Connection connection, Path path, DatasetPackageTables.Table table)
      throws IOException, SQLException {
    String names = String.join(",", table.columns());
    String placeholders =
        String.join(
            ",",
            table.columns().stream()
                .map(
                    name -> DatasetPackageTables.TYPES.get(name).equals("jsonb") ? "?::jsonb" : "?")
                .toList());
    String sql = "INSERT INTO " + table.name() + " (" + names + ") VALUES (" + placeholders + ")";
    try (var insert = connection.prepareStatement(sql);
        var lines = new DatasetPackageCodec.BoundedLines(path)) {
      int batch = 0;
      String line;
      while ((line = lines.next()) != null) {
        var row = DatasetPackageCodec.validateRow(DatasetPackageCodec.parseJson(line), table);
        for (int i = 0; i < table.columns().size(); i++) {
          String column = table.columns().get(i);
          bind(connection, insert, i + 1, DatasetPackageTables.TYPES.get(column), row.get(column));
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
      Connection connection, PreparedStatement insert, int index, String type, Object value)
      throws SQLException, IOException {
    if (value == null) {
      int sqlType =
          switch (type) {
            case "bigint" -> Types.BIGINT;
            case "integer" -> Types.INTEGER;
            case "smallint" -> Types.SMALLINT;
            case "numeric" -> Types.NUMERIC;
            case "uuid" -> Types.OTHER;
            default -> Types.VARCHAR;
          };
      insert.setNull(index, sqlType);
      return;
    }
    switch (type) {
      case "smallint" -> insert.setShort(index, (short) DatasetPackageCodec.integer(value, 0));
      case "integer" ->
          insert.setInt(index, Math.toIntExact(DatasetPackageCodec.integer(value, 0)));
      case "bigint" -> insert.setLong(index, DatasetPackageCodec.integer(value, 0));
      case "numeric" -> insert.setBigDecimal(index, (BigDecimal) value);
      case "boolean" -> insert.setBoolean(index, (Boolean) value);
      case "uuid" -> insert.setObject(index, UUID.fromString((String) value));
      case "timestamptz" -> insert.setObject(index, OffsetDateTime.parse((String) value));
      case "jsonb" ->
          insert.setString(
              index, new String(DatasetPackageCodec.jsonBytes(value), StandardCharsets.UTF_8));
      case "text[]" ->
          insert.setArray(
              index, connection.createArrayOf("text", DatasetPackageCodec.array(value).toArray()));
      default -> insert.setString(index, (String) value);
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
                "SELECT lexicon_version, preparation_policy, entry_count, lookup_count "
                    + "FROM lexicon_dataset WHERE dataset_id=1")) {
      if (!result.next()
          || result.getLong(1) != manifest.datasetVersion()
          || !result.getString(2).equals(manifest.preparationPolicy())
          || result.getLong(3) != manifest.files().get(1).rows()
          || result.getLong(4) != manifest.files().get(2).rows()
          || result.next()) throw invalid();
    }
  }

  private static void compareExisting(
      Connection connection, DatasetPackageCodec.Verified verified, byte[] sql)
      throws SQLException, IOException {
    Path output = Files.createTempFile("lexiflow-existing-", ".zip").toRealPath();
    Files.delete(output);
    try {
      PostgresDatasetExport.export(connection, output, verified.file("approvals.json"), sql, false);
      if (!DatasetPackageCodec.sha256(output).equals(verified.sha256())) throw invalid();
    } finally {
      Files.deleteIfExists(output);
    }
  }

  private static IOException invalid() {
    return new IOException(BAD);
  }
}
