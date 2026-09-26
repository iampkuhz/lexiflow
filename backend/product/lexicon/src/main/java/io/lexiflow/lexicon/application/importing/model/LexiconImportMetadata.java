package io.lexiflow.lexicon.application.importing.model;

import java.time.Instant;
import java.util.Objects;

/**
 * 一个离线词库来源批次的可审计元数据。
 *
 * @param sourceDigest 含义：来源文件的内容摘要。取值范围：由方法调用前置条件限定。
 * @param sourceId 含义：来源标识。取值范围：由方法调用前置条件限定。
 * @param licenseId 含义：来源许可证标识。取值范围：由方法调用前置条件限定。
 * @param acquiredAt 含义：获取来源的时刻。取值范围：由方法调用前置条件限定。
 */
public record LexiconImportMetadata(
    String sourceDigest, String sourceId, String licenseId, Instant acquiredAt) {
  /** 构造不可为空的导入元数据。 */
  public LexiconImportMetadata {
    sourceDigest = required(sourceDigest, "sourceDigest");
    sourceId = required(sourceId, "sourceId");
    licenseId = required(licenseId, "licenseId");
    acquiredAt = Objects.requireNonNull(acquiredAt, "acquiredAt");
  }

  private static String required(String value, String field) {
    var required = Objects.requireNonNull(value, field).trim();
    if (required.isEmpty()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return required;
  }
}
