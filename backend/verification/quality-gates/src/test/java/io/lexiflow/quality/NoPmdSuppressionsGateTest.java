package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class NoPmdSuppressionsGateTest {

  @TempDir Path directory;

  @Test
  void acceptsNonPmdSuppressWarnings() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "no-pmd-suppressions/positive.fixture", "PmdPositive.java");

    assertTrue(new NoPmdSuppressionsGate().evaluate(context).isEmpty());
  }

  @Test
  void rejectsPmdSuppressWarningsWithRuleAttribute() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "no-pmd-suppressions/negative.fixture", "PmdNegative.java");

    var violations = new NoPmdSuppressionsGate().evaluate(context);

    assertEquals(1, violations.size());
    assertEquals("PMD_SUPPRESSION_FORBIDDEN", violations.getFirst().code());
    assertEquals("PMD.CloseResource", violations.getFirst().attributes().get("pmdRule"));
  }
}
