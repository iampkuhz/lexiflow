package io.lexiflow.quality;

import java.util.List;
import java.util.Objects;

/**
 * 一条规则的一次类型化执行结果。
 *
 * @param rule 规则标识。
 * @param status 执行状态。
 * @param violations 已稳定排序的违规。
 */
public record GateEvaluation(String rule, GateStatus status, List<QualityViolation> violations) {

  /** 校验状态与违规数量一致，并冻结违规列表。 */
  public GateEvaluation {
    Objects.requireNonNull(rule, "rule");
    Objects.requireNonNull(status, "status");
    violations = violations.stream().sorted().toList();
    if ((violations.isEmpty()) != (status == GateStatus.PASS)) {
      throw new IllegalArgumentException("gate status must match violations");
    }
  }

  /** 从规则输出构造状态一致的结果。 */
  public static GateEvaluation from(String rule, List<QualityViolation> violations) {
    return new GateEvaluation(
        rule, violations.isEmpty() ? GateStatus.PASS : GateStatus.FAIL, violations);
  }
}
