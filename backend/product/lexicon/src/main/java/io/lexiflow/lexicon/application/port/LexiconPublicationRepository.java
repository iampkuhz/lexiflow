package io.lexiflow.lexicon.application.port;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import java.io.IOException;
import java.util.function.Consumer;

/** 仅接收应用层已准备词条的原子发布持久化角色。 */
public interface LexiconPublicationRepository {
  /** 在新版本事务内产生已准备条目。 */
  @FunctionalInterface
  interface PreparedEntrySource {
    /**
     * 按实际发布版本交付准备结果。
     *
     * @param publishedVersion 含义：本事务分配的版本。取值范围：正整数。
     * @param consumer 含义：持久化条目接收器。取值范围：非 null。
     * @throws IOException 来源读取失败
     */
    void read(long publishedVersion, Consumer<LexiconImportPlan.PlannedEntry> consumer)
        throws IOException;
  }

  /** 事务内部的真实进度；实现必须隔离进度回调异常。 */
  interface PublicationProgress {
    /** 通知事务持久化阶段已实际开始。 */
    void persistStarted();

    /**
     * 通知批量写入和实际行数核验已完成。
     *
     * @param entries 实际刷新的词条数。
     * @param lookups 实际生成的 lookup 投影行数。
     */
    void persisted(long entries, long lookups);

    /** 通知事务管理器确认发生回滚。 */
    void rolledBack();

    /** 不观察事务进度的显式空消费者。 */
    PublicationProgress NONE =
        new PublicationProgress() {
          @Override
          public void persistStarted() {}

          @Override
          public void persisted(long entries, long lookups) {}

          @Override
          public void rolledBack() {}
        };
  }

  /**
   * 按实际事务版本原子发布预检来源。
   *
   * @param metadata 含义：来源及策略元数据。取值范围：非 null。
   * @param sourceRowsTotal 含义：原始来源行数。取值范围：正整数。
   * @param expectedEntries 含义：预检条目数。取值范围：正整数且不大于来源行数。
   * @param source 含义：应用层准备结果生产者。取值范围：非 null。
   * @return 已提交的新版本
   */
  default long publish(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      PreparedEntrySource source) {
    return publish(metadata, sourceRowsTotal, expectedEntries, source, PublicationProgress.NONE);
  }

  /**
   * 事务进度主入口；实现者必须提供实际完成/回滚回调。 提交版本只在事务执行器正常返回后向应用交付。
   *
   * @param metadata 含义：来源身份及策略元数据。取值范围：非 null。
   * @param sourceRowsTotal 含义：预检来源原始行数。取值范围：正整数。
   * @param expectedEntries 含义：预检可导入条目数。取值范围：正整数且不大于来源行数。
   * @param source 含义：事务内准备结果生产者。取值范围：非 null。
   * @param progress 含义：事务内实际阶段与回滚回调。取值范围：非 null。
   * @return 已经提交的词库版本。
   */
  long publish(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      PreparedEntrySource source,
      PublicationProgress progress);
}
