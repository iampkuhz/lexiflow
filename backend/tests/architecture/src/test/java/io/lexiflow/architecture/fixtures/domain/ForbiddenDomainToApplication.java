package io.lexiflow.architecture.fixtures.domain;

import io.lexiflow.architecture.fixtures.application.AllowedApplication;

/** 隔离 fixture 的非法 domain 反向依赖 application。 */
public final class ForbiddenDomainToApplication {
  /** 返回 application 类型以构造反向依赖边。 */
  public AllowedApplication application() {
    return new AllowedApplication();
  }
}
