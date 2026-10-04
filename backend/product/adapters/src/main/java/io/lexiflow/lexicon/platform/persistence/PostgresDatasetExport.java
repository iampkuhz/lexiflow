package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 单一只读 REPEATABLE READ 快照导出；不调用词库业务规则或旧导入链。 */
public final class PostgresDatasetExport {
  private PostgresDatasetExport() {}

  /**
   * 导出结果仅暴露包摘要和原发布版本。
   *
   * @param sha256 含义：最终 ZIP 的 SHA-256。取值范围：64 位小写十六进制。
   * @param datasetVersion 含义：既有已发布资料版本。取值范围：正整数。
   */
  public record Result(String sha256, long datasetVersion) {}

  /**
   * 从一个明确 JDBC 连接导出一份一致性资料包。
   *
   * @param connection 含义：独立 JDBC 连接。取值范围：已发布 Lexicon 三表。
   * @param output 含义：最终 ZIP 文件路径。取值范围：同内容可复用，异内容拒绝。
   * @param approvalFile 含义：责任人显式审批文件。取值范围：严格 JSON 且不含符号链接。
   * @param schemaSql 含义：组合根内嵌可信 SQL。取值范围：非包内输入。
   * @return 最终 ZIP 摘要和原资料版本。
   */
  public static Result export(
      Connection connection, Path output, Path approvalFile, byte[] schemaSql)
      throws IOException, SQLException {
    return export(connection, output, approvalFile, schemaSql, true, () -> {});
  }

  static Result export(
      Connection connection,
      Path output,
      Path approvalFile,
      byte[] schemaSql,
      boolean manageTransaction)
      throws IOException, SQLException {
    return export(connection, output, approvalFile, schemaSql, manageTransaction, () -> {});
  }

  static Result export(
      Connection connection,
      Path output,
      Path approvalFile,
      byte[] schemaSql,
      boolean manageTransaction,
      Runnable afterDataset)
      throws IOException, SQLException {
    DatasetPackageCodec.rejectSymlinkPath(approvalFile);
    if (!Files.isRegularFile(approvalFile, LinkOption.NOFOLLOW_LINKS)
        || Files.size(approvalFile) > DatasetPackageCodec.MAX_METADATA_BYTES) throw invalid();
    byte[] approvalBytes = Files.readAllBytes(approvalFile);
    var approvals = DatasetPackageCodec.parseApprovals(approvalBytes);
    Path parent = output.toAbsolutePath().normalize().getParent();
    DatasetPackageCodec.rejectSymlinkPath(output);
    if (parent == null || !Files.isDirectory(parent)) throw invalid();
    Path dir = Files.createTempDirectory(parent, ".lexiflow-export-");
    boolean oldAuto = connection.getAutoCommit();
    int oldIsolation = connection.getTransactionIsolation();
    boolean oldReadOnly = connection.isReadOnly();
    try {
      if (manageTransaction) {
        connection.setAutoCommit(false);
        connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
        connection.setReadOnly(true);
      }
      var records = new ArrayList<DatasetPackageManifest.FileRecord>();
      Map<String, Object> dataset = null;
      for (int i = 0; i < DatasetPackageTables.TABLES.size(); i++) {
        var table = DatasetPackageTables.TABLES.get(i);
        String name = DatasetPackageCodec.ENTRY_NAMES.get(i + 2);
        Path file = dir.resolve(name);
        long rows = 0;
        try (var statement =
                connection.prepareStatement(
                    "SELECT "
                        + String.join(",", table.columns())
                        + " FROM "
                        + table.name()
                        + " ORDER BY "
                        + table.orderBy(),
                    ResultSet.TYPE_FORWARD_ONLY,
                    ResultSet.CONCUR_READ_ONLY);
            var outputStream = Files.newOutputStream(file)) {
          statement.setFetchSize(500);
          try (var result = statement.executeQuery()) {
            while (result.next()) {
              Map<String, Object> row = row(result, table);
              DatasetPackageCodec.validateRow(row, table);
              byte[] encoded = DatasetPackageCodec.jsonBytes(row);
              if (encoded.length > DatasetPackageCodec.MAX_LINE_BYTES) throw invalid();
              outputStream.write(encoded);
              outputStream.write('\n');
              if (i == 0) dataset = row;
              rows++;
            }
          }
        }
        if (rows < 1 || (i == 0 && rows != 1)) throw invalid();
        records.add(
            new DatasetPackageManifest.FileRecord(
                name, Files.size(file), DatasetPackageCodec.sha256(file), rows));
        if (i == 0) afterDataset.run();
      }
      if (dataset == null
          || DatasetPackageCodec.integer(dataset.get("dataset_id"), 1) != 1
          || !HintPreparation.POLICY_ID.equals(dataset.get("preparation_policy"))
          || DatasetPackageCodec.integer(dataset.get("entry_count"), 1) != records.get(1).rows()
          || DatasetPackageCodec.integer(dataset.get("lookup_count"), 1) != records.get(2).rows()
          || DatasetPackageCodec.integer(dataset.get("source_row_count"), 1)
              < records.get(1).rows()) throw invalid();
      DatasetPackageCodec.validateSources(dataset.get("source_manifest"), approvals);
      validateProjection(connection);
      long version = DatasetPackageCodec.integer(dataset.get("lexicon_version"), 1);
      var files = new ArrayList<Map<String, Object>>();
      for (var record : records) {
        var file = new LinkedHashMap<String, Object>();
        file.put("name", record.name());
        file.put("bytes", record.bytes());
        file.put("sha256", record.sha256());
        file.put("rows", record.rows());
        files.add(file);
      }
      var manifest = new LinkedHashMap<String, Object>();
      manifest.put("schemaVersion", 2);
      manifest.put("schemaSha256", DatasetPackageCodec.sha256(schemaSql));
      manifest.put("approvalSha256", DatasetPackageCodec.sha256(approvalBytes));
      manifest.put("datasetVersion", version);
      manifest.put("preparationPolicy", dataset.get("preparation_policy"));
      manifest.put("files", files);
      Files.write(dir.resolve("manifest.json"), DatasetPackageCodec.jsonBytes(manifest));
      Files.write(dir.resolve("approvals.json"), approvalBytes);
      Path candidate = dir.resolve("candidate.zip");
      DatasetPackageCodec.write(
          candidate,
          List.of(
              dir.resolve("manifest.json"),
              dir.resolve("approvals.json"),
              dir.resolve("dataset.ndjson"),
              dir.resolve("entries.ndjson"),
              dir.resolve("forms.ndjson")));
      String hash = DatasetPackageCodec.sha256(candidate);
      DatasetPackageCodec.verify(candidate, hash, schemaSql);
      DatasetPackageCodec.publishVerified(candidate, output);
      if (manageTransaction) connection.commit();
      return new Result(hash, version);
    } catch (IOException | SQLException | RuntimeException exception) {
      if (manageTransaction) connection.rollback();
      throw exception;
    } finally {
      if (manageTransaction) {
        connection.setReadOnly(oldReadOnly);
        connection.setTransactionIsolation(oldIsolation);
        connection.setAutoCommit(oldAuto);
      }
      Files.deleteIfExists(dir.resolve("candidate.zip"));
      for (String name : DatasetPackageCodec.ENTRY_NAMES) Files.deleteIfExists(dir.resolve(name));
      Files.deleteIfExists(dir);
    }
  }

  static Map<String, Object> row(ResultSet result, DatasetPackageTables.Table table)
      throws SQLException, IOException {
    var row = new LinkedHashMap<String, Object>();
    for (String column : table.columns()) {
      Object raw = result.getObject(column);
      Object value;
      if (raw == null) value = null;
      else
        switch (DatasetPackageTables.TYPES.get(column)) {
          case "smallint", "integer", "bigint" -> value = raw;
          case "numeric" -> value = ((BigDecimal) raw).stripTrailingZeros();
          case "boolean" -> value = raw;
          case "uuid" -> value = raw.toString();
          case "timestamptz" ->
              value = result.getObject(column, OffsetDateTime.class).toInstant().toString();
          case "text[]" -> {
            Object[] array = (Object[]) result.getArray(column).getArray();
            value = java.util.Arrays.asList(array);
          }
          case "jsonb" ->
              value =
                  DatasetPackageCodec.parseJson(raw.toString().getBytes(StandardCharsets.UTF_8));
          default -> value = result.getString(column);
        }
      row.put(column, value);
    }
    return row;
  }

  static void validateProjection(Connection connection) throws SQLException, IOException {
    String sql =
        "SELECT COUNT(*) FROM lexicon_form f LEFT JOIN lexicon_entry e USING (entry_id) "
            + "WHERE e.entry_id IS NULL OR f.normalized_form IS NULL";
    try (var statement = connection.createStatement();
        var result = statement.executeQuery(sql)) {
      if (!result.next() || result.getLong(1) != 0) throw invalid();
    }
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT COUNT(*) FROM lexicon_entry e WHERE NOT EXISTS "
                    + "(SELECT 1 FROM lexicon_form f WHERE f.entry_id=e.entry_id)")) {
      if (!result.next() || result.getLong(1) != 0) throw invalid();
    }
  }

  private static IOException invalid() {
    return new IOException("invalid package");
  }
}
