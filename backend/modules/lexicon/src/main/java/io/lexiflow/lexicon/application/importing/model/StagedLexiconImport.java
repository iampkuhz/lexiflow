package io.lexiflow.lexicon.application.importing.model;

import java.util.Objects;
import java.util.UUID;

/**
 * 可恢复的不可见词库版本及其已提交来源行边界。
 *
 * @param batchId 含义：导入批次标识。取值范围：由方法调用前置条件限定。
 * @param version 含义：尚未发布的词库版本。取值范围：由方法调用前置条件限定。
 * @param sourceRowsProcessed 含义：已成功写入的来源行边界。取值范围：由方法调用前置条件限定。
 */
public record StagedLexiconImport(UUID batchId, long version, long sourceRowsProcessed) {
  /** 构造有效的 staged 批次标识。 */
  public StagedLexiconImport {
    batchId = Objects.requireNonNull(batchId, "batchId");
    if (version < 1 || sourceRowsProcessed < 0) {
      throw new IllegalArgumentException("staged import values are invalid");
    }
  }
}
