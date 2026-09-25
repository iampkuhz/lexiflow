package io.lexiflow.lexicon.application.importing.model;

import java.util.Objects;

/**
 * 词库字段可追溯的来源与记录引用。
 *
 * @param sourceId 含义：来源标识。取值范围：由方法调用前置条件限定。
 * @param licenseId 含义：许可证标识。取值范围：由方法调用前置条件限定。
 * @param recordReference 含义：来源中的记录定位。取值范围：由方法调用前置条件限定。
 */
public record SourceReference(String sourceId, String licenseId, String recordReference) {
  /** 构造不可为空的来源引用。 */
  public SourceReference {
    sourceId = required(sourceId, "sourceId");
    licenseId = required(licenseId, "licenseId");
    recordReference = required(recordReference, "recordReference");
  }

  private static String required(String value, String field) {
    var required = Objects.requireNonNull(value, field).trim();
    if (required.isEmpty()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return required;
  }
}
