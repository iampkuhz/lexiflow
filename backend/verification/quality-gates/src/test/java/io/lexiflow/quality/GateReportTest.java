package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** 状态只由冻结证据派生，JSON 保持固定外形及字段顺序。 */
class GateReportTest {

  @Test
  void derivesPassingStateAndPreservesJsonShape() {
    var evaluation = new GateEvaluation("probe", List.of());
    var report = new GateReport(3, List.of(evaluation));
    assertEquals(GateStatus.PASS, evaluation.status());
    assertEquals(GateStatus.PASS, report.status());
    assertEquals(
        "{\"schemaVersion\":\"lexiflow.java-source-gates.v1\",\"status\":\"PASS\",\"sourceCount\":3,"
            + "\"evaluations\":[{\"rule\":\"probe\",\"status\":\"PASS\",\"violations\":[]}]}\n",
        report.toJson());
  }

  @Test
  void derivesFailureFromFrozenSortedViolations() {
    var violations = new ArrayList<QualityViolation>();
    violations.add(new QualityViolation("probe", "z.java", 2, "BAD", "中文说明", Map.of()));
    violations.add(new QualityViolation("probe", "a.java", 1, "BAD", "中文说明", Map.of()));
    var evaluation = new GateEvaluation("probe", violations);
    violations.clear();
    var evaluations = new ArrayList<>(List.of(new GateEvaluation("ok", List.of()), evaluation));
    var report = new GateReport(2, evaluations);
    evaluations.clear();
    assertEquals(GateStatus.FAIL, evaluation.status());
    assertEquals(GateStatus.FAIL, report.status());
    assertEquals(
        List.of("a.java", "z.java"),
        evaluation.violations().stream().map(QualityViolation::path).toList());
    assertEquals(2, report.evaluations().size());
    assertThrows(UnsupportedOperationException.class, () -> evaluation.violations().clear());
    assertThrows(UnsupportedOperationException.class, () -> report.evaluations().clear());
    assertEquals(
        "{\"schemaVersion\":\"lexiflow.java-source-gates.v1\",\"status\":\"FAIL\",\"sourceCount\":2,"
            + "\"evaluations\":[{\"rule\":\"ok\",\"status\":\"PASS\",\"violations\":[]},"
            + "{\"rule\":\"probe\",\"status\":\"FAIL\",\"violations\":["
            + "{\"path\":\"a.java\",\"line\":1,\"code\":\"BAD\",\"message\":\"中文说明\",\"attributes\":{}},"
            + "{\"path\":\"z.java\",\"line\":2,\"code\":\"BAD\",\"message\":\"中文说明\",\"attributes\":{}}]}]}\n",
        report.toJson());
  }

  @Test
  void rejectsInvalidReportInputsAndMissingRule() {
    assertThrows(
        IllegalArgumentException.class,
        () -> new GateReport(-1, List.of(new GateEvaluation("probe", List.of()))));
    assertThrows(IllegalArgumentException.class, () -> new GateReport(0, List.of()));
    assertThrows(NullPointerException.class, () -> new GateEvaluation(null, List.of()));
  }
}
