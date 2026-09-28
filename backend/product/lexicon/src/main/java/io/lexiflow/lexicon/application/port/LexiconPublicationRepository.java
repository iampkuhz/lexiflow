package io.lexiflow.lexicon.application.port;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;

/** 词库发布持久化角色；不暴露表、DAO 或数据库连接。 */
public interface LexiconPublicationRepository {
  /**
   * 原子地持久化并发布一个规范词库版本。
   *
   * @param request 含义：完整且已验证的导入输入。取值范围：非 null。
   * @return 新发布的词库版本。
   */
  long publish(LexiconImportRequest request);

  /**
   * 在单个事务中写入预检来源并完整切换。
   *
   * @param metadata 含义：来源、许可证、摘要与取得时间。取值范围：非 null。
   * @param sourceRowsTotal 含义：预检原始来源行数。取值范围：正整数。
   * @param expectedEntries 含义：预检可导入词条数。取值范围：正整数且不超过来源行数。
   * @param source 含义：事务内重读来源。取值范围：非 null。
   * @return 完整发布的新版本。
   */
  long publishStreaming(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconImportRowSource source);
}
