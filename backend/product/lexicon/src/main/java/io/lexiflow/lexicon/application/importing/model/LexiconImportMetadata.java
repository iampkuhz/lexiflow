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
 * @param generation 含义：已校验来源与代码的生成身份。取值范围：非空有效记录。
 */
public record LexiconImportMetadata(
    String sourceDigest,
    String sourceId,
    String licenseId,
    Instant acquiredAt,
    GenerationIdentity generation) {
  /** 构造不可为空的导入元数据。 */
  public LexiconImportMetadata {
    sourceDigest = required(sourceDigest, "sourceDigest");
    sourceId = required(sourceId, "sourceId");
    licenseId = required(licenseId, "licenseId");
    acquiredAt = Objects.requireNonNull(acquiredAt, "acquiredAt");
    generation = Objects.requireNonNull(generation, "generation");
  }

  /**
   * 显式绑定到已校验来源与执行期代码身份的生成记录。
   *
   * @param codeVersion 含义：生成逻辑代码摘要。取值范围：64 位小写十六进制。
   * @param sourceDigest 含义：来源及上游身份摘要。取值范围：64 位小写十六进制。
   * @param generationVersion 含义：完整生成身份摘要。取值范围：64 位小写十六进制。
   * @param inputSha256 含义：原始 CSV 字节摘要。取值范围：64 位小写十六进制。
   * @param configJson 含义：固定来源与许可配置。取值范围：非空规范 JSON。
   * @param inputsJson 含义：输入摘要及上游身份。取值范围：非空规范 JSON。
   */
  public record GenerationIdentity(
      String codeVersion,
      String sourceDigest,
      String generationVersion,
      String inputSha256,
      String configJson,
      String inputsJson) {
    /** 校验生成摘要及必要元数据，不接受缺失身份或空配置。 */
    public GenerationIdentity {
      codeVersion = validHash(codeVersion, "codeVersion");
      sourceDigest = validHash(sourceDigest, "generationSourceDigest");
      generationVersion = validHash(generationVersion, "generationVersion");
      inputSha256 = validHash(inputSha256, "inputSha256");
      configJson = required(configJson, "configJson");
      inputsJson = required(inputsJson, "inputsJson");
    }

    private static String validHash(String value, String field) {
      if (value == null || !value.matches("[0-9a-f]{64}"))
        throw new IllegalArgumentException(field);
      return value;
    }
  }

  private static String required(String value, String field) {
    var required = Objects.requireNonNull(value, field).trim();
    if (required.isEmpty()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return required;
  }
}
