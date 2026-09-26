package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.port.LexiconRepository;
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
   * 发布已完成前置扫描的来源；读取与写入在一个事务中完成。
   *
   * @param metadata 含义：来源、许可证、摘要和取得时间。取值范围：非 null，摘要与来源已核对。
   * @param sourceRowsTotal 含义：前置扫描得到的原始来源行数。取值范围：大于等于可导入词条数的正整数。
   * @param expectedEntries 含义：前置扫描得到的可导入词条数。取值范围：大于零且不超过来源行数。
   * @param source 含义：在事务中重读相同来源并逐条交付的函数。取值范围：非 null，不得静默丢弃已解析记录。
   * @return 完整发布的新版本。
   */
  public long publishStreaming(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconImportRowSource source) {
    return repository.publishStreaming(
        Objects.requireNonNull(metadata, "metadata"),
        sourceRowsTotal,
        expectedEntries,
        Objects.requireNonNull(source, "source"));
  }
}
