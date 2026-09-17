package io.lexiflow.architecture.fixtures.application;

import io.lexiflow.architecture.fixtures.domain.ValidDomain;

/** 隔离 fixture 的合法 application 到 domain 依赖。 */
public final class AllowedApplication {
  /** 返回 domain 类型，构造隔离合法依赖边。 */
  public ValidDomain domain() {
    return new ValidDomain();
  }
}
