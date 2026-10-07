package io.lexiflow.lexicon.platform.persistence;

import java.io.IOException;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.Instant;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** 对已发布成功记录执行共享的 schema、约束、索引、行数、关联与摘要校验。 */
public final class PostgresDatasetVerifier {
  private PostgresDatasetVerifier() {}

  /**
   * 校验当前 schema 的唯一发布资料；不把权限或连接错误解释为空库。
   *
   * @param connection 含义：目标 schema 的数据库连接。取值范围：已授权连接。
   */
  public static Publication verify(Connection connection) throws SQLException, IOException {
    if (!PostgresDatasetPackage.relations(connection).equals(PostgresDatasetPackage.RELATIONS))
      throw invalid();
    PostgresDatasetPackage.assertShape(connection);
    long version, entries, forms, sourceRows;
    String code, source, generation, config, inputs, content;
    try (var s = connection.createStatement();
        var r =
            s.executeQuery(
                "SELECT lexicon_version,entry_count,lookup_count,source_row_count,generation_code_version,generation_source_digest,generation_version,generation_config::text,generation_inputs::text,content_sha256,preparation_policy,source_manifest::text FROM lexicon_dataset WHERE dataset_id=1")) {
      if (!r.next()) throw invalid();
      version = r.getLong(1);
      entries = r.getLong(2);
      forms = r.getLong(3);
      sourceRows = r.getLong(4);
      code = r.getString(5).trim();
      source = r.getString(6).trim();
      generation = r.getString(7).trim();
      config = r.getString(8);
      inputs = r.getString(9);
      content = r.getString(10).trim();
      if (version < 1
          || version > 9_007_199_254_740_991L
          || entries < 1
          || forms < entries
          || sourceRows < entries
          || !io.lexiflow.lexicon.application.importing.policy.HintPreparation.POLICY_ID.equals(
              r.getString(11))) throw invalid();
      verifySources(r.getString(12));
      if (r.next()) throw invalid();
    }
    if (entries != count(connection, "lexicon_entry")
        || forms != count(connection, "lexicon_form")
        || count(
                connection,
                "SELECT COUNT(*) FROM lexicon_entry e WHERE NOT EXISTS (SELECT 1 FROM lexicon_form f WHERE f.entry_id=e.entry_id)")
            != 0
        || count(
                connection,
                "SELECT COUNT(*) FROM lexicon_form f LEFT JOIN lexicon_entry e USING(entry_id) WHERE e.entry_id IS NULL")
            != 0
        || !content.equals(DatasetContentChecksum.calculate(connection))) throw invalid();
    DatasetPackageCodec.verifyGeneration(
        code,
        source,
        generation,
        new String(
            DatasetPackageCodec.jsonBytes(DatasetPackageCodec.parseJson(config)),
            java.nio.charset.StandardCharsets.UTF_8),
        new String(
            DatasetPackageCodec.jsonBytes(DatasetPackageCodec.parseJson(inputs)),
            java.nio.charset.StandardCharsets.UTF_8));
    return new Publication(version, generation, entries, forms, content);
  }

  private static void verifySources(String json) throws IOException {
    var seen = new HashSet<String>();
    var items = DatasetPackageCodec.array(DatasetPackageCodec.parseJson(json));
    if (items.isEmpty()) throw invalid();
    for (Object item : items) {
      Map<String, Object> source =
          DatasetPackageCodec.object(
              item, Set.of("source_id", "license_id", "source_digest", "acquired_at"));
      String id = DatasetPackageCodec.string(source.get("source_id"));
      DatasetPackageCodec.string(source.get("license_id"));
      if (!seen.add(id)) throw invalid();
      if (!DatasetPackageCodec.digestValue(source.get("source_digest")).matches("[0-9a-f]{64}"))
        throw invalid();
      try {
        Instant.parse(DatasetPackageCodec.string(source.get("acquired_at")));
      } catch (RuntimeException failure) {
        throw invalid(failure);
      }
    }
  }

  private static long count(Connection connection, String table) throws SQLException {
    String sql = table.startsWith("SELECT ") ? table : "SELECT COUNT(*) FROM " + table;
    try (var s = connection.createStatement();
        var r = s.executeQuery(sql)) {
      r.next();
      return r.getLong(1);
    }
  }

  private static IOException invalid() {
    return invalid(null);
  }

  private static IOException invalid(Throwable cause) {
    return new IOException("invalid published dataset", cause);
  }

  /**
   * 完整发布资料的身份与计数。
   *
   * @param datasetVersion 含义：发布版本。取值范围：正安全整数。
   * @param generationVersion 含义：生成摘要。取值范围：64 位小写十六进制。
   * @param entryCount 含义：词条数量。取值范围：正整数。
   * @param formCount 含义：词形数量。取值范围：不小于词条数。
   * @param contentSha256 含义：实际内容摘要。取值范围：64 位小写十六进制。
   */
  public record Publication(
      long datasetVersion,
      String generationVersion,
      long entryCount,
      long formCount,
      String contentSha256) {}
}
