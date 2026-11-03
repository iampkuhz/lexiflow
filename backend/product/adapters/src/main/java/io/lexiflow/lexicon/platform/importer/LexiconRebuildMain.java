package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.platform.persistence.SqliteSchemaInitializer;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;

/** 本机开发库的显式结构重建与完整词库发布入口。 */
public final class LexiconRebuildMain {

  private LexiconRebuildMain() {}

  /**
   * 预检来源，确认目标后重建本项目表并发布；不删除本机文件。
   *
   * @param args 含义：JDBC 地址、最新 SQL、StarDict CSV；取值范围：三个非空参数
   * @throws Exception 来源、目标或数据库操作失败时抛出
   */
  public static void main(String[] args) throws Exception {
    if (args.length != 3 || args[0].isBlank() || args[1].isBlank() || args[2].isBlank()) {
      throw new IllegalArgumentException("usage: <jdbc-url> <schema-file> <stardict-csv>");
    }
    var jdbcUrl = args[0];
    var schemaFile = Path.of(args[1]);
    var input = args[2];
    System.out.println(
        "[lexiconRebuild] 这是单个 Gradle 任务；以下 4 个阶段及其内部步骤会分别标记。执行时每 3 分钟报告状态；等待输入时不输出心跳，不代表完成百分比。");
    try (var progress = new RebuildProgress(System.out, TimeUnit.MINUTES.toMillis(3))) {
      progress.start(1, "来源预检", "只读，不连接数据库");
      LexiconImportMain.execute(new String[] {"validate", "--input", input}, progress::detail);
      progress.complete();

      progress.start(2, "目标检查与确认", "读取 SQLite 文件身份，尚未删除数据");
      var target = SqliteSchemaInitializer.inspect(jdbcUrl);
      System.out.printf(
          "目标文件：%s；已有词库对象：%s；其他对象：%s%n",
          target.database(), target.lexiconRelations(), target.otherObjects());
      if (!target.lexiconRelations().isEmpty() || !target.otherObjects().isEmpty()) {
        var expected = confirmation(target.database());
        progress.awaitInput(
            "将删除并重建本项目词库表及其数据。目标文件存在非本项目对象时拒绝重建。请先停止 API、确认备份和目标文件。"
                + " 不会删除 CSV、其他文件或其他表。\n"
                + "请在运行此命令的终端输入："
                + expected
                + "，然后按 Enter。\n"
                + "不重建请按 Ctrl+C；输入其他文本或直接按 Enter 也会取消。\n"
                + "> ");
        requireConfirmation(
            target.database(), new InputStreamReader(System.in, StandardCharsets.UTF_8));
        progress.resumeExecution("已收到精确确认；重新核对目标，尚未删除数据");
      }
      if (!target.equals(SqliteSchemaInitializer.inspect(jdbcUrl))) {
        throw new IllegalStateException(
            "database target changed after confirmation; no database changes");
      }
      progress.complete();

      progress.start(3, "结构重建", "单个事务内只处理本项目词库表");
      SqliteSchemaInitializer.rebuild(jdbcUrl, schemaFile);
      progress.complete();

      progress.start(4, "全量导入与发布", "发布前扫描、批量写入及提交属于同一发布操作");
      LexiconImportMain.execute(
          new String[] {
            "publish",
            "--input",
            input,
            "--database-url",
            jdbcUrl,
            "--batch-source-id",
            "ecdict-stardict",
            "--batch-license-id",
            "MIT"
          },
          progress::detail);
      progress.complete();
    }
  }

  static String confirmation(String database) {
    return "REBUILD " + database;
  }

  static void requireConfirmation(String database, Reader input) throws IOException {
    var answer = new BufferedReader(input).readLine();
    if (answer == null) {
      throw new IllegalStateException("未收到确认输入（stdin 已关闭）；未修改数据库。请在可输入的终端重新运行。");
    }
    if (!confirmation(database).equals(answer)) {
      throw new IllegalStateException("确认文本不匹配，已取消重建；未修改数据库。");
    }
  }
}
