package io.lexiflow.quality;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Gradle JavaExec 使用的最小命令入口。 */
public final class QualityGateMain {

  private QualityGateMain() {}

  /**
   * 执行固定规则集并先写报告，再按报告状态决定进程结果。
   *
   * @param args 后端根目录与报告文件两个参数。
   * @throws Exception 输入无效、读取失败或发现违规。
   */
  public static void main(String[] args) throws Exception {
    if (args.length != 2) {
      throw new IllegalArgumentException("expected backend root and report path");
    }
    var report = JavaSourceGateRunner.run(Path.of(args[0]));
    var reportPath = Path.of(args[1]);
    Files.createDirectories(reportPath.getParent());
    Files.writeString(reportPath, report.toJson(), StandardCharsets.UTF_8);
    if (report.status() != GateStatus.PASS) {
      throw new IllegalStateException("Java source gates failed; see " + reportPath);
    }
  }
}
