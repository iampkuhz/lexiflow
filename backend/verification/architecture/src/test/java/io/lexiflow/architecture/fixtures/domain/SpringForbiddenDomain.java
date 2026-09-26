package io.lexiflow.architecture.fixtures.domain;

import org.springframework.context.ApplicationContext;

/** 隔离 fixture 的非法 domain Spring 依赖。 */
public final class SpringForbiddenDomain {
  /** 返回 Spring 类型以构造边界反例。 */
  public ApplicationContext context() {
    return null;
  }
}
