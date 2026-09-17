package io.lexiflow.architecture.fixtures.api;

import org.springframework.context.ApplicationContext;

/** 隔离 fixture 的非法伪组合根 Spring 依赖。 */
public final class SpringFalseCompositionRoot {
  /** 返回 Spring 类型以构造伪组合根反例。 */
  public ApplicationContext context() {
    return null;
  }
}
