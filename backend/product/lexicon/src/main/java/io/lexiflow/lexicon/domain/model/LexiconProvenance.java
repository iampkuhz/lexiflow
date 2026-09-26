package io.lexiflow.lexicon.domain.model;

import java.time.Instant;
import java.util.Objects;

/**
 * 一个已发布词库版本的来源、许可和完整性声明。
 *
 * @param sourceId 来源系统的稳定标识。
 * @param licenseId 适用的许可标识。
 * @param contentDigest 来源材料的完整性摘要。
 * @param acquiredAt 获得或发布该材料的时间。
 */
public record LexiconProvenance(
    String sourceId, String licenseId, String contentDigest, Instant acquiredAt) {

  /** 校验发布所需的可复核来源信息。 */
  public LexiconProvenance {
    sourceId = required(sourceId, "sourceId");
    licenseId = required(licenseId, "licenseId");
    contentDigest = required(contentDigest, "contentDigest");
    Objects.requireNonNull(acquiredAt, "acquiredAt");
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }
}
