package io.lexiflow.quality;

import java.util.List;

/** 接收显式源码上下文且不读取 Git 或启动外部进程的 Java 门禁。 */
public interface JavaSourceGate {

  /** 返回稳定规则标识。 */
  String id();

  /** 对共享解析结果执行确定性检查。 */
  List<QualityViolation> evaluate(JavaSourceContext context);
}
