package io.lexiflow.quality;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;

/** 以固定顺序共享输入并执行三条 LexiFlow Java source gate。 */
public final class JavaSourceGateRunner {

  private static final List<JavaSourceGate> RULES =
      List.of(
          new JavaCommentLanguageGate(),
          new RecordComponentJavadocGate(),
          new NoPmdSuppressionsGate());

  private JavaSourceGateRunner() {}

  /**
   * 从显式后端根目录读取源码并执行全部规则。
   *
   * @param root 后端构建根目录。
   * @return 类型化确定性报告。
   * @throws IOException 源码读取或 compiler parse 失败。
   */
  public static GateReport run(Path root) throws IOException {
    var allSources = JavaSourceDiscovery.discover(root);
    var parsed = ParsedJavaSources.parse(root, allSources);
    var context = new JavaSourceContext(allSources, parsed);
    var evaluations =
        RULES.stream().map(rule -> GateEvaluation.from(rule.id(), rule.evaluate(context))).toList();
    var status =
        evaluations.stream().allMatch(item -> item.status() == GateStatus.PASS)
            ? GateStatus.PASS
            : GateStatus.FAIL;
    return new GateReport(status, allSources.size(), evaluations);
  }

  /**
   * 对测试提供的显式源码上下文执行全部规则。
   *
   * @param context 已解析的共享输入。
   * @return 类型化确定性报告。
   */
  public static GateReport evaluate(JavaSourceContext context) {
    var evaluations =
        RULES.stream().map(rule -> GateEvaluation.from(rule.id(), rule.evaluate(context))).toList();
    var status =
        evaluations.stream().allMatch(item -> item.status() == GateStatus.PASS)
            ? GateStatus.PASS
            : GateStatus.FAIL;
    return new GateReport(status, context.allSources().size(), evaluations);
  }
}
