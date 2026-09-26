package io.lexiflow.architecture.fixtures.domain;

import java.time.Instant;

/** 隔离 fixture 的合法纯 JDK domain 依赖。 */
public final class ExternalLibraryAllowedDomain {
  /** 返回不属于运行时 adapter 的值类型。 */
  public Instant now() {
    return Instant.now();
  }
}
