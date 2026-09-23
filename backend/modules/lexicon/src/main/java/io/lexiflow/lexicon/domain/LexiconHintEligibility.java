package io.lexiflow.lexicon.domain;

/** 发布资料中的预处理资格；CANDIDATE 不是语境正确性保证。 */
public enum LexiconHintEligibility {
  /** 尚未按提示策略处理，观看时不提示。 */
  UNPROCESSED,
  /** 导入时标记的基础词，包含其别名和词形。 */
  BASIC_VOCABULARY,
  /** 允许进入后续释义安全、冲突和数量筛选。 */
  CANDIDATE
}
