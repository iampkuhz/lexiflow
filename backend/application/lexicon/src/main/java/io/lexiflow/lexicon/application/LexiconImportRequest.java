package io.lexiflow.lexicon.application;

import java.util.List;
import java.util.Objects;

/**
 * 一次需要作为完整新版本发布的规范词库输入。
 *
 * @param rows 含义：已验证的完整来源行。取值范围：由方法调用前置条件限定。
 * @param metadata 含义：来源的可审计元数据。取值范围：由方法调用前置条件限定。
 */
public record LexiconImportRequest(List<LexiconImportRow> rows, LexiconImportMetadata metadata) {
  /** 构造非空的完整导入请求。 */
  public LexiconImportRequest {
    rows = List.copyOf(Objects.requireNonNull(rows, "rows"));
    if (rows.isEmpty()) {
      throw new IllegalArgumentException("rows must not be empty");
    }
    metadata = Objects.requireNonNull(metadata, "metadata");
  }
}
