package io.lexiflow.architecture.fixtures.application.importing.validation;

import io.lexiflow.lexicon.application.importing.LexiconImportService;

/** 架构测试隔离样例，通过签名形成待验证的依赖边。 */
public final class ForbiddenValidationService {
  private ForbiddenValidationService() {}

  /**
   * 保留输入类型以验证静态依赖，不执行业务。
   *
   * @param value 含义：受测依赖实例。取值范围：可为空。
   * @return 原输入实例。
   */
  public static LexiconImportService echo(LexiconImportService value) {
    return value;
  }
}
