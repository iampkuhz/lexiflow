package io.lexiflow.api.fixtures;

import org.springframework.context.ApplicationContext;

/** 隔离 fixture 的允许 Spring composition-root 依赖。 */
public final class SpringAllowedCompositionRoot {
  /** 返回 Spring 类型以构造允许边界。 */
  public ApplicationContext context() {
    return null;
  }
}
