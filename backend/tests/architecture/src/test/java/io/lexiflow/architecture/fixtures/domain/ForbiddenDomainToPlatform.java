package io.lexiflow.architecture.fixtures.domain;

import io.lexiflow.architecture.fixtures.platform.PlatformAdapter;

/** 隔离 fixture 的非法 domain 到平台依赖。 */
public final class ForbiddenDomainToPlatform {
  /** 返回具体平台类型，构造隔离反例的依赖边。 */
  public PlatformAdapter adapter() {
    return new PlatformAdapter();
  }
}
