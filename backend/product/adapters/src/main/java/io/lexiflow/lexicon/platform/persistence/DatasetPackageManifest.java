package io.lexiflow.lexicon.platform.persistence;

import java.util.List;

/**
 * 已发布词库资料包的封闭元数据结构。
 *
 * @param schemaVersion 含义：资料包格式版本。取值范围：当前仅 1。
 * @param schemaSha256 含义：可信 SQL 摘要。取值范围：64 位小写十六进制。
 * @param approvalSha256 含义：审批文件摘要。取值范围：64 位小写十六进制。
 * @param datasetVersion 含义：数据库原发布版本。取值范围：正整数。
 * @param preparationPolicy 含义：既有准备策略身份。取值范围：当前固定策略 ID。
 * @param files 含义：按固定顺序排列的三份 NDJSON 记录。取值范围：恰好三项。
 */
public record DatasetPackageManifest(
    int schemaVersion,
    String schemaSha256,
    String approvalSha256,
    long datasetVersion,
    String preparationPolicy,
    List<FileRecord> files) {

  /**
   * 一个固定 NDJSON 负载的摘要与行数。
   *
   * @param name 含义：固定条目名。取值范围：三份 NDJSON 之一。
   * @param bytes 含义：未压缩字节数。取值范围：正整数。
   * @param sha256 含义：未压缩内容摘要。取值范围：64 位小写十六进制。
   * @param rows 含义：有效 JSON 行数。取值范围：正整数。
   */
  public record FileRecord(String name, long bytes, String sha256, long rows) {}
}
