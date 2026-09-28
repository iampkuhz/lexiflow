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

  /**
   * 按实际事务版本原子发布预检来源。
   *
   * @param metadata 含义：来源及策略元数据。取值范围：非 null。
   * @param sourceRowsTotal 含义：原始来源行数。取值范围：正整数。
   * @param expectedEntries 含义：预检条目数。取值范围：正整数且不大于来源行数。
   * @param source 含义：应用层准备结果生产者。取值范围：非 null。
   * @return 已提交的新版本
   */
  long publish(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      PreparedEntrySource source);
}
