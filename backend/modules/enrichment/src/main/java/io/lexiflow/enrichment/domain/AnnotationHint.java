package io.lexiflow.enrichment.domain;

import java.util.Objects;

/**
 * 与当前字幕范围和词库版本绑定的可展示中文提示。
 *
 * @param startOffset 词段半开区间起点。
 * @param endOffset 词段半开区间终点。
 * @param lexiconEntryId 候选词条稳定标识。
 * @param lexiconVersion 候选词条版本。
 * @param chineseGloss 语境中的简短中文释义。
 */
public record AnnotationHint(
    int startOffset,
    int endOffset,
    String lexiconEntryId,
    long lexiconVersion,
    String chineseGloss) {

  /** 校验提示引用与词库版本。 */
  public AnnotationHint {
    if (startOffset < 0 || endOffset <= startOffset) {
      throw new IllegalArgumentException("hint range is invalid");
    }
    lexiconEntryId = required(lexiconEntryId, "lexiconEntryId");
    if (lexiconVersion < 1) {
      throw new IllegalArgumentException("lexiconVersion must be positive");
    }
    chineseGloss = required(chineseGloss, "chineseGloss");
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }
}
