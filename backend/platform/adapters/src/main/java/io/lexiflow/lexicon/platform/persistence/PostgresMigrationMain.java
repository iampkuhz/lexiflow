package io.lexiflow.lexicon.platform.persistence;

import java.nio.file.Path;

/**
 * 后端原生 CLI：显式执行 PostgreSQL migration 序列。
 *
 * <p>该入口不创建数据库；调用方须传入受隔离的目标及迁移目录。
 */
public final class PostgresMigrationMain {
  private PostgresMigrationMain() {}

  /**
   * 执行调用方明确给出的 migration 参数。
   *
   * @param args 含义：JDBC 地址、迁移目录和可选目标版本；取值范围：二至三个非空参数
   * @throws Exception 读取参数、连接数据库或执行 SQL 失败时抛出
   */
  public static void main(String[] args) throws Exception {
    if (args.length < 2 || args.length > 3) {
      throw new IllegalArgumentException("usage: <jdbc-url> <migrations-dir> [target-version]");
    }
    var targetVersion = args.length == 3 ? Integer.valueOf(args[2]) : null;
    var applied = PostgresSchemaMigrator.apply(args[0], Path.of(args[1]), targetVersion);
    System.out.println("PASS postgres migrations applied=" + applied);
  }
}
