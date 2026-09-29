package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.port.LexiconImportObserver;
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
    return publishStreaming(
        metadata,
        sourceRowsTotal,
        expectedEntries,
        source,
        new LexiconImportObservation(LexiconImportObserver.NONE));
  }

  /**
   * 执行流式发布并报告实际事务边界。 仅提交返回后输出成功终态，原始异常仍由调用者接收。
   *
   * @param metadata 含义：来源身份与策略元数据。取值范围：非 null。
   * @param sourceRowsTotal 含义：预检确认的来源原始行数。取值范围：正整数。
   * @param expectedEntries 含义：预检确认的可导入条目数。取值范围：正整数且不大于来源行数。
   * @param source 含义：发布事务内重读的来源。取值范围：非 null。
   * @param observation 含义：当前 publish run 的观察状态。取值范围：非 null。
   * @return 已由事务提交返回确认的发布版本。
   */
  public long publishStreaming(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconImportRowSource source,
      LexiconImportObservation observation) {
    Objects.requireNonNull(metadata, "metadata");
    Objects.requireNonNull(source, "source");
    if (sourceRowsTotal < 1 || expectedEntries < 1 || expectedEntries > sourceRowsTotal)
      throw new IllegalArgumentException("invalid completed source counts");
    Objects.requireNonNull(observation, "observation");
    var persisted = new long[2];
    var persistedKnown = new boolean[1];
    var rolledBack = new boolean[1];
    var sourceFailure = new Throwable[1];
    observation.start(LexiconImportObserver.Step.PERSIST);
    try {
      long publishedVersion =
          repository.publish(
              metadata,
              sourceRowsTotal,
              expectedEntries,
              (version, consumer) -> {
                var validator = LexiconImportPlan.canonicalSurfaceValidator();
                var delivered = new long[1];
                var consumerFailure = new RuntimeException[1];
                final LexiconImportRowSource.ReadReceipt receipt;
                try {
                  receipt =
                      source.read(
                          row -> {
                            Objects.requireNonNull(row, "source row");
                            var planned =
                                LexiconImportPlan.prepareNext(
                                    row,
                                    version,
                                    metadata.sourceDigest(),
                                    metadata.acquiredAt(),
                                    validator);
                            try {
                              consumer.accept(planned);
                            } catch (RuntimeException exception) {
                              consumerFailure[0] = exception;
                              throw exception;
                            }
                            delivered[0]++;
                          });
                } catch (IOException | RuntimeException exception) {
                  if (consumerFailure[0] == null) sourceFailure[0] = exception;
                  throw exception;
                }
                if (receipt == null
                    || !metadata.sourceDigest().equals(receipt.sourceDigest())
                    || receipt.sourceRowsTotal() != sourceRowsTotal
                    || delivered[0] != expectedEntries) throw new LexiconSourceChangedException();
              },
              new LexiconPublicationRepository.PublicationProgress() {
                @Override
                public void persistStarted() {
                  observation.heartbeat(LexiconImportObserver.Step.PERSIST);
                }

                @Override
                public void persisted(long entries, long lookups) {
                  persisted[0] = entries;
                  persisted[1] = lookups;
                  persistedKnown[0] = true;
                  observation.persisted(entries, lookups);
                  observation.start(LexiconImportObserver.Step.PUBLISH);
                }

                @Override
                public void rolledBack() {
                  rolledBack[0] = true;
                }
              });
      observation.complete(
          LexiconImportObserver.Step.PERSIST,
          LexiconImportObserver.Reason.OK,
          java.util.Map.of(),
          java.util.Map.of());
      if (!persistedKnown[0]) observation.start(LexiconImportObserver.Step.PUBLISH);
      observation.complete(
          LexiconImportObserver.Step.PUBLISH,
          LexiconImportObserver.Reason.OK,
          java.util.Map.of(),
          java.util.Map.of());
      observation.published(
          persistedKnown[0] ? persisted[0] : null,
          persistedKnown[0] ? persisted[1] : null,
          publishedVersion);
      return publishedVersion;
    } catch (RuntimeException failure) {
      var reason = classify(failure, rolledBack[0], sourceFailure[0]);
      observation.complete(
          LexiconImportObserver.Step.PUBLISH, reason, java.util.Map.of(), java.util.Map.of());
      observation.terminal(reason, java.util.Map.of(), java.util.Map.of(), null);
      throw failure;
    }
  }

  private static LexiconImportObserver.Reason classify(
      RuntimeException failure, boolean rolledBack, Throwable sourceFailure) {
    if (failure instanceof LexiconSourceChangedException)
      return LexiconImportObserver.Reason.SOURCE_CHANGED;
    if (failure instanceof java.util.concurrent.CancellationException
        || sourceFailure instanceof java.io.InterruptedIOException)
      return LexiconImportObserver.Reason.CANCELLED;
    if (sourceFailure instanceof java.io.UncheckedIOException io) {
      if (io.getCause() instanceof java.io.InterruptedIOException)
        return LexiconImportObserver.Reason.CANCELLED;
      return LexiconImportObserver.Reason.SOURCE_INVALID;
    }
    if (sourceFailure instanceof IllegalArgumentException || sourceFailure instanceof IOException)
      return LexiconImportObserver.Reason.SOURCE_INVALID;
    if (rolledBack) return LexiconImportObserver.Reason.PUBLISH_ROLLED_BACK;
    return LexiconImportObserver.Reason.INTERNAL_ERROR;
  }
}
