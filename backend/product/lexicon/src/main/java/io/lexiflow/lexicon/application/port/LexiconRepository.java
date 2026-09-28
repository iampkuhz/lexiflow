package io.lexiflow.lexicon.application.port;

import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.Collection;
import java.util.List;

/** 词库聚合的唯一持久化合同；调用者不接触表、DAO、DO 或 PostgreSQL 类型。 */
public interface LexiconRepository extends LexiconPublicationRepository {
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
}
