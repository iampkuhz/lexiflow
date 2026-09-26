package io.lexiflow.lexicon.application.port;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.Collection;
import java.util.List;

/** 词库聚合的唯一持久化合同；调用者不接触表、DAO、DO 或 PostgreSQL 类型。 */
public interface LexiconRepository {
  /**
   * 返回当前已发布词库版本；没有已发布批次时返回 0。
   *
   * @return 供查询使用的已发布版本
   */
  long publishedVersion();

  /**
   * 对一组规范化词形仅从观看查询投影批量读取最终提示或阻断决定。
   *
   * @param version 含义：已发布词库版本。取值范围：由方法调用前置条件限定。
   * @param forms 含义：已规范化、待查的表面集合。取值范围：由方法调用前置条件限定。
   * @return 每个匹配词形的完整候选集合，包括阻断和歧义行。
   */
  List<LexiconHintCandidate> findByForms(long version, Collection<String> forms);

  /**
   * 返回指定版本内按动作选出的可预热词形及其完整歧义集合。
   *
   * @param version 含义：已发布词库版本。取值范围：由方法调用前置条件限定。
   * @param action 含义：正向提示或负向阻断。取值范围：非空。
   * @param limit 含义：预热词形的上限。取值范围：非负整数。
   * @return 按预热优先级排序且不丢失同形歧义的词形集合。
   */
  List<LexiconHintCandidate> findPrewarmForms(long version, LexiconHintAction action, int limit);

  /**
   * 原子地持久化并发布一个规范词库版本。
   *
   * @param request 含义：完整且已验证的导入输入。取值范围：由方法调用前置条件限定。
   * @return 新发布的词库版本
   */
  long publish(LexiconImportRequest request);

  /**
   * 将已预检来源在单个事务中批量写入并完整切换；不保存中间状态。
   *
   * @param metadata 含义：来源、许可证、摘要和取得时间。取值范围：非 null，摘要与来源已核对。
   * @param sourceRowsTotal 含义：前置扫描得到的原始来源行数。取值范围：大于等于可导入词条数的正整数。
   * @param expectedEntries 含义：前置扫描得到的可导入词条数。取值范围：大于零且不超过来源行数。
   * @param source 含义：在事务内重读来源的有界内存行提供者。取值范围：非 null，不得静默丢弃已解析记录。
   * @return 完整发布的新版本。
   */
  long publishStreaming(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconImportRowSource source);
}
