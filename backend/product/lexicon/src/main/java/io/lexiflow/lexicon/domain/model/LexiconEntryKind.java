package io.lexiflow.lexicon.domain.model;

/** 词条可检索表面的固定类别。 */
public enum LexiconEntryKind {
  /** 单一英文 token。 */
  WORD,
  /** 两个或更多英文 token 组成的固定短语。 */
  PHRASE
}
