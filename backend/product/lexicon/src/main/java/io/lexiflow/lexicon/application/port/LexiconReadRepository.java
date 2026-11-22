package io.lexiflow.lexicon.application.port;

import io.lexiflow.lexicon.domain.model.LexiconBrowsePage;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.Collection;
import java.util.List;

/** 词库读取角色；只暴露已发布查询，不暴露发布写入能力。 */
public interface LexiconReadRepository {
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
   * @return 按统一正优先级排序且不丢失同形歧义的词形集合。
   */
  List<LexiconHintCandidate> findPrewarmForms(long version, LexiconHintAction action, int limit);

  /**
   * 按规范词形或别名精确匹配已发布词条，并执行稳定分页。
   *
   * @param normalizedExactQuery 含义：规范化后的精确词形查询。取值范围：非空或空串；空串表示不筛选。
   * @param frequencySort 含义：是否按频率排序。取值范围：true 表示频率优先，false 表示字母序。
   * @param page 含义：结果页码。取值范围：从 1 开始的正整数。
   * @param pageSize 含义：每页条数。取值范围：正整数。
   * @return 稳定排序的已发布词条页及分页元数据。
   */
  default LexiconBrowsePage browse(
      String normalizedExactQuery, boolean frequencySort, int page, int pageSize) {
    return new LexiconBrowsePage(List.of(), 0, page, pageSize);
  }

  /**
   * Returns published displayable entries for exact stable identities.
   *
   * @param entryIds 含义：待查的稳定词条身份。取值范围：正 long 集合。
   * @return 存在且有可展示释义的已发布词条；未知或无释义身份不返回。
   */
  default List<io.lexiflow.lexicon.domain.model.LexiconBrowseEntry> findDisplayableByIds(
      Collection<Long> entryIds) {
    return List.of();
  }
}
