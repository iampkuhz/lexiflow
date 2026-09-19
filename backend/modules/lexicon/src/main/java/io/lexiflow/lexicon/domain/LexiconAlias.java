package io.lexiflow.lexicon.domain;

import java.util.Objects;

/**
 * 指向同一词条的规范化别名，不构成独立义项。
 *
 * @param normalizedForm 已规范化的别名表面。
 */
public record LexiconAlias(String normalizedForm) {

  /** 规范化别名表面，拒绝空值与非规范输入。 */
  public LexiconAlias {
    normalizedForm = LexiconEntry.normalizeEnglishForm(normalizedForm, "normalizedForm");
    Objects.requireNonNull(normalizedForm, "normalizedForm");
  }
}
