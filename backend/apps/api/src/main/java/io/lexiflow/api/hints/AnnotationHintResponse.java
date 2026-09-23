package io.lexiflow.api.hints;

import java.util.Objects;
import java.util.UUID;

/**
 * API 返回的一条版本化词段中文提示。
 *
 * @param startOffset 词段区间起点。
 * @param endOffset 词段区间终点。
 * @param lexiconEntryId 候选词条标识。
 * @param senseId 已确定义项标识。
 * @param lexiconVersion 候选词条版本。
 * @param chineseGloss 简短中文释义。
 */
public record AnnotationHintResponse(
    int startOffset,
    int endOffset,
    String lexiconEntryId,
    String senseId,
    long lexiconVersion,
    String chineseGloss) {

  /** 校验响应引用保留领域层提供的 canonical UUID 身份。 */
  public AnnotationHintResponse {
    lexiconEntryId = canonicalUuid(lexiconEntryId, "lexiconEntryId");
    senseId = canonicalUuid(senseId, "senseId");
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
}
