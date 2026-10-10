package io.lexiflow.quality;

import java.util.List;
import java.util.Objects;

/**
 * 一条规则的一次类型化执行结果。
 *
 * @param rule 规则标识。
 * @param violations 已稳定排序的违规。
 */
public record GateEvaluation(String rule, List<QualityViolation> violations) {

  /** 冻结并稳定排序违规列表。 */
  public GateEvaluation {
    Objects.requireNonNull(rule, "rule");
    violations = violations.stream().sorted().toList();
  }

  /** 从违规数量派生执行状态。 */
  public GateStatus status() {
    return violations.isEmpty() ? GateStatus.PASS : GateStatus.FAIL;
  }
}
