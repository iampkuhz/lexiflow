package io.lexiflow.architecture.fixtures.domain;

import java.net.http.HttpClient;
import java.sql.Connection;

/** 隔离 fixture 的非法 HTTP 与数据库依赖。 */
public final class ExternalLibraryForbiddenDomain {
  /** 返回 JDK HTTP 类型以构造依赖边。 */
  public HttpClient httpClient() {
    return HttpClient.newHttpClient();
  }

  /** 返回 JDK SQL 类型以构造依赖边。 */
  public Connection connection() {
    return null;
  }
}
