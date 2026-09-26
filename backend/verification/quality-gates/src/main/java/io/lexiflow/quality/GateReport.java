package io.lexiflow.quality;

import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 固定规则顺序的 Java source gate 报告。
 *
 * @param status 整体状态。
 * @param sourceCount 输入源码数量。
 * @param evaluations 各规则类型化结果。
 */
public record GateReport(GateStatus status, int sourceCount, List<GateEvaluation> evaluations) {

  /** 校验整体状态并冻结规则结果。 */
  public GateReport {
    Objects.requireNonNull(status, "status");
    evaluations = List.copyOf(evaluations);
    if (sourceCount < 0 || evaluations.isEmpty()) {
      throw new IllegalArgumentException("gate report must contain rule evaluations");
    }
    var hasFailure = evaluations.stream().anyMatch(item -> item.status() == GateStatus.FAIL);
    if (hasFailure != (status == GateStatus.FAIL)) {
      throw new IllegalArgumentException("report status must match evaluations");
    }
  }

  /** 生成稳定字段与数组顺序的 JSON。 */
  public String toJson() {
    var output = new StringBuilder();
    output.append("{\"schemaVersion\":\"lexiflow.java-source-gates.v1\"");
    output.append(",\"status\":\"").append(status).append('\"');
    output.append(",\"sourceCount\":").append(sourceCount);
    output.append(",\"evaluations\":[");
    for (var index = 0; index < evaluations.size(); index++) {
      if (index > 0) {
        output.append(',');
      }
      appendEvaluation(output, evaluations.get(index));
    }
    output.append("]}\n");
    return output.toString();
  }

  private static void appendEvaluation(StringBuilder output, GateEvaluation evaluation) {
    output.append("{\"rule\":").append(quote(evaluation.rule()));
    output.append(",\"status\":").append(quote(evaluation.status().name()));
    output.append(",\"violations\":[");
    for (var index = 0; index < evaluation.violations().size(); index++) {
      if (index > 0) {
        output.append(',');
      }
      appendViolation(output, evaluation.violations().get(index));
    }
    output.append("]}");
  }

  private static void appendViolation(StringBuilder output, QualityViolation violation) {
    output.append("{\"path\":").append(quote(violation.path()));
    output.append(",\"line\":").append(violation.line());
    output.append(",\"code\":").append(quote(violation.code()));
    output.append(",\"message\":").append(quote(violation.message()));
    output.append(",\"attributes\":{");
    var index = 0;
    for (Map.Entry<String, String> entry : violation.attributes().entrySet()) {
      if (index++ > 0) {
        output.append(',');
      }
      output.append(quote(entry.getKey())).append(':').append(quote(entry.getValue()));
    }
    output.append("}}");
  }

  private static String quote(String value) {
    var output = new StringBuilder("\"");
    for (var index = 0; index < value.length(); index++) {
      var character = value.charAt(index);
      switch (character) {
        case '\\' -> output.append("\\\\");
        case '"' -> output.append("\\\"");
        case '\n' -> output.append("\\n");
        case '\r' -> output.append("\\r");
        case '\t' -> output.append("\\t");
        default -> {
          if (character < 0x20) {
            output.append(String.format("\\u%04x", (int) character));
          } else {
            output.append(character);
          }
        }
      }
    }
    return output.append('\"').toString();
  }
}
