package io.lexiflow.lexicon.domain.port;

import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import java.util.List;

/** 为提示规则提供版本化词汇材料的公开合同。 */
public interface LexiconCatalog {

  /**
   * 按已规范化的精确词形键返回本次查询结果。
   *
   * @param normalizedForms 含义：Enrichment 生成的规范词形键。取值范围：非 null；每键非空白、已经规范化且至多三个 token。
   * @return 候选、可选发布身份及本次请求局部计数。
   */
  LexiconLookupResult lookupForms(List<String> normalizedForms);
}
