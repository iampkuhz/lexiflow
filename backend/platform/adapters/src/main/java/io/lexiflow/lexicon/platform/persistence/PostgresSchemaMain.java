package io.lexiflow.lexicon.platform.persistence;

import java.nio.file.Path;

/**
 * 后端原生 CLI：显式初始化 PostgreSQL schema。
 *
 * <p>该入口不创建数据库；调用方须传入受隔离的目标及 SQL 文件。
 */
public final class PostgresSchemaMain {

  private PostgresSchemaMain() {}

  /**
   * 执行调用方明确给出的初始化参数。
   *
   * @param args 含义：JDBC 地址和 SQL 文件；取值范围：两个非空参数
   * @throws Exception 读取参数、连接数据库或执行 SQL 失败时抛出
   */
  public static void main(String[] args) throws Exception {
    if (args.length != 2) {
      throw new IllegalArgumentException("usage: <jdbc-url> <schema-file>");
    }
    PostgresSchemaInitializer.initialize(args[0], Path.of(args[1]));
    System.out.println("PASS postgres schema initialized");
  }
}
