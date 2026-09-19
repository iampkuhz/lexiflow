package io.lexiflow.lexicon.domain;

import java.util.Objects;
import java.util.UUID;

/**
 * 词条一个不可变、可引用的义项。
 *
 * @param senseId 义项稳定 UUID。
 * @param chineseGloss 面向提示展示的简短中文释义。
 * @param definition 义项最小定义。
 * @param provenanceReference 指向来源材料的可复核引用。
 */
public record LexiconSense(
    UUID senseId, String chineseGloss, String definition, String provenanceReference) {

  /** 校验义项身份、最小定义和可复核来源引用。 */
  public LexiconSense {
    Objects.requireNonNull(senseId, "senseId");
    chineseGloss = required(chineseGloss, "chineseGloss");
    definition = optional(definition, "definition");
    provenanceReference = required(provenanceReference, "provenanceReference");
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }

  private static String optional(String value, String field) {
    Objects.requireNonNull(value, field);
    return value;
  }
}
