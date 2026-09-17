package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class RecordComponentJavadocGateTest {

  @TempDir Path directory;

  @Test
  void acceptsChineseRecordComponentDescription() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "record-component-javadocs/positive.fixture", "RecordPositive.java");

    assertTrue(new RecordComponentJavadocGate().evaluate(context).isEmpty());
  }

  @Test
  void rejectsNonChineseRecordComponentDescription() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "record-component-javadocs/negative.fixture", "RecordNegative.java");

    var violations = new RecordComponentJavadocGate().evaluate(context);

    assertEquals(1, violations.size());
    assertEquals("RECORD_COMPONENT_PARAM_NOT_CHINESE", violations.getFirst().code());
  }
}
