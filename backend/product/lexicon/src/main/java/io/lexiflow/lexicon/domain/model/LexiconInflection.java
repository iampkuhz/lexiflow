package io.lexiflow.lexicon.domain.model;

import java.util.Objects;

/**
 * 指向同一 lemma 的规范化英文屈折形，不构成独立义项。
 *
 * @param normalizedForm 已规范化的屈折形表面。
 */
public record LexiconInflection(String normalizedForm) {

  /** 规范化屈折形表面，拒绝空值与非规范输入。 */
  public LexiconInflection {
    normalizedForm = LexiconEntry.normalizeEnglishForm(normalizedForm, "normalizedForm");
    Objects.requireNonNull(normalizedForm, "normalizedForm");
  }
}
