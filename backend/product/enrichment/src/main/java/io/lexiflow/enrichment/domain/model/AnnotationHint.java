package io.lexiflow.enrichment.domain.model;

import java.util.Objects;
import java.util.UUID;

/**
 * 与当前字幕范围和词库版本绑定的可展示中文提示。
 *
 * @param startOffset 含义：提示词段半开区间起点。取值范围：非负 UTF-16 偏移。
 * @param endOffset 含义：提示词段半开区间终点。取值范围：大于 startOffset 的 UTF-16 偏移。
 * @param lexiconEntryId 含义：候选词条稳定身份。取值范围：正 long 的规范十进制字符串。
 * @param senseId 含义：候选词条内已确定的义项身份。取值范围：规范 UUID。
 * @param lexiconVersion 含义：候选词条所属版本。取值范围：正 long。
 * @param chineseGloss 含义：语境中的简短中文释义。取值范围：非空白字符串。
 * @param lemma 含义：匹配词条的真实规范主词形。取值范围：非空白字符串。
 */
public record AnnotationHint(
    int startOffset,
    int endOffset,
    String lexiconEntryId,
    String senseId,
    long lexiconVersion,
    String chineseGloss,
    String lemma) {

  /** 校验提示引用与词库版本。 */
  public AnnotationHint {
    if (startOffset < 0 || endOffset <= startOffset) {
      throw new IllegalArgumentException("hint range is invalid");
    }
    lexiconEntryId = canonicalPositiveLong(lexiconEntryId, "lexiconEntryId");
    senseId = canonicalUuid(senseId, "senseId");
    if (lexiconVersion < 1) {
      throw new IllegalArgumentException("lexiconVersion must be positive");
    }
    chineseGloss = required(chineseGloss, "chineseGloss");
    lemma = required(lemma, "lemma");
  }

  /** 构造未提供规范 lemma 的旧式测试值。 */
  public AnnotationHint(
      int start, int end, String entryId, String senseId, long version, String gloss) {
    this(start, end, entryId, senseId, version, gloss, "unknown");
  }

  private static String canonicalUuid(String value, String field) {
    Objects.requireNonNull(value, field);
    try {
      if (value.isBlank() || !UUID.fromString(value).toString().equals(value)) {
        throw new IllegalArgumentException(field + " must be a canonical UUID");
      }
      return value;
    } catch (IllegalArgumentException exception) {
      throw new IllegalArgumentException(field + " must be a canonical UUID", exception);
    }
  }

  private static String canonicalPositiveLong(String value, String field) {
    Objects.requireNonNull(value, field);
    try {
      long parsed = Long.parseLong(value);
      if (parsed <= 0 || !Long.toString(parsed).equals(value)) {
        throw new IllegalArgumentException(field + " must be a canonical positive long");
      }
      return value;
    } catch (NumberFormatException exception) {
      throw new IllegalArgumentException(field + " must be a canonical positive long", exception);
    }
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }
}
