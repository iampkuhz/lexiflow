package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import java.io.IOException;
import java.util.Objects;

/** 协调离线词库准备与发布，不包含文件格式、SQL 或连接逻辑。 */
public final class LexiconImportService {
  private final LexiconPublicationRepository repository;

  /** 使用发布持久化角色构造导入服务。 */
  public LexiconImportService(LexiconPublicationRepository repository) {
    this.repository = Objects.requireNonNull(repository, "repository");
  }

  /**
   * 将内存请求包装为冻结来源并执行同一流式准备发布链。
   *
   * @param request 含义：冻结的规范来源行及元数据。取值范围：非 null、至少一条来源行。
   * @return 原子提交的新版本。
   */
  public long publish(LexiconImportRequest request) {
    Objects.requireNonNull(request, "request");
    var metadata = request.metadata();
    return publishStreaming(
        metadata,
        request.rows().size(),
        request.rows().size(),
        consumer -> {
          request.rows().forEach(consumer);
          return new LexiconImportRowSource.ReadReceipt(
              metadata.sourceDigest(), request.rows().size());
        });
  }

  /**
   * 在数据库事务回调中逐行准备并核对重读来源。
   *
   * @param metadata 含义：来源、许可证、摘要和获取时间。取值范围：非 null。
   * @param sourceRowsTotal 含义：预检原始行数。取值范围：正整数。
   * @param expectedEntries 含义：预检条目数。取值范围：正整数且不大于来源行数。
   * @param source 含义：重读解析来源。取值范围：非 null。
   * @return 已发布新版本
   */
  public long publishStreaming(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconImportRowSource source) {
    Objects.requireNonNull(metadata, "metadata");
    Objects.requireNonNull(source, "source");
    if (sourceRowsTotal < 1 || expectedEntries < 1 || expectedEntries > sourceRowsTotal)
      throw new IllegalArgumentException("invalid completed source counts");
    return repository.publish(
        metadata,
        sourceRowsTotal,
        expectedEntries,
        (version, consumer) -> {
          var validator = LexiconImportPlan.canonicalSurfaceValidator();
          var delivered = new long[1];
          final LexiconImportRowSource.ReadReceipt receipt;
          try {
            receipt =
                source.read(
                    row -> {
                      Objects.requireNonNull(row, "source row");
                      consumer.accept(
                          LexiconImportPlan.prepareNext(
                              row,
                              version,
                              metadata.sourceDigest(),
                              metadata.acquiredAt(),
                              validator));
                      delivered[0]++;
                    });
          } catch (IOException exception) {
            throw exception;
          }
          if (receipt == null
              || !metadata.sourceDigest().equals(receipt.sourceDigest())
              || receipt.sourceRowsTotal() != sourceRowsTotal
              || delivered[0] != expectedEntries)
            throw new IllegalStateException("source changed during publication");
        });
  }
}
