package io.lexiflow.lexicon.domain.port;

import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.List;

/** 为提示规则提供版本化词汇材料的公开合同。 */
public interface LexiconCatalog {

  /**
   * 返回可用于给定字幕的候选词汇；调用方不得修改返回集合。
   *
   * @param caption 含义：已规范化或待规范化的英文字幕。取值范围：非空，可为空白字符串。
   * @return 候选词条，按调用方可稳定处理的顺序返回。
   */
  List<LexiconHintCandidate> candidatesFor(String caption);
}
