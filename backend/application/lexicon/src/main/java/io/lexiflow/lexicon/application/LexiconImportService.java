package io.lexiflow.lexicon.application;

import java.util.List;
import java.util.Objects;

/** 协调离线词库导入与发布，不包含文件格式、SQL 或连接逻辑。 */
public final class LexiconImportService {
  private final LexiconRepository repository;

  /** 使用唯一词库 Repository 构造导入服务。 */
  public LexiconImportService(LexiconRepository repository) {
    this.repository = Objects.requireNonNull(repository, "repository");
  }

  /**
   * 发布已完成验证的规范导入。
   *
   * @param request 含义：完整且已验证的导入输入。取值范围：由方法调用前置条件限定。
   * @return 新发布的词库版本
   */
  public long publish(LexiconImportRequest request) {
    return repository.publish(Objects.requireNonNull(request, "request"));
  }

  /**
   * 取得可恢复的流式导入批次。
   *
   * @param metadata 含义：来源的可审计元数据。取值范围：由方法调用前置条件限定。
   * @return 可继续写入的 staged 批次
   */
  public StagedLexiconImport openOrResume(LexiconImportMetadata metadata) {
    return repository.openOrResume(Objects.requireNonNull(metadata, "metadata"));
  }

  /**
   * 写入一个流式来源分块。
   *
   * @param batch 含义：要续接的 staged 批次。取值范围：由方法调用前置条件限定。
   * @param rows 含义：本次来源行。取值范围：由方法调用前置条件限定。
   * @param processedThrough 含义：本次写入后的来源行边界。取值范围：由方法调用前置条件限定。
   * @param metadata 含义：来源的可审计元数据。取值范围：由方法调用前置条件限定。
   */
  public void stage(
      StagedLexiconImport batch,
      List<LexiconImportRow> rows,
      long processedThrough,
      LexiconImportMetadata metadata) {
    repository.stage(
        Objects.requireNonNull(batch, "batch"),
        List.copyOf(Objects.requireNonNull(rows, "rows")),
        processedThrough,
        Objects.requireNonNull(metadata, "metadata"));
  }

  /**
   * 发布已完成扫描的流式批次。
   *
   * @param batch 含义：要发布的 staged 批次。取值范围：由方法调用前置条件限定。
   * @param sourceRowsTotal 含义：已扫描的来源行总数。取值范围：由方法调用前置条件限定。
   */
  public void publish(StagedLexiconImport batch, long sourceRowsTotal) {
    repository.publish(Objects.requireNonNull(batch, "batch"), sourceRowsTotal);
  }
}
