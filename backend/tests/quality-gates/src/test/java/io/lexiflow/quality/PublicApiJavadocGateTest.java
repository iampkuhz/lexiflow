package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class PublicApiJavadocGateTest {

  @TempDir Path directory;

  @Test
  void acceptsMultilineSummaryAndStructuredParameterDocumentation() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "public-api-javadocs/positive.fixture", "ApiPositive.java");

    assertTrue(new PublicApiJavadocGate().evaluate(context).isEmpty());
  }

  @Test
  void rejectsSingleLineDocumentationAndIncompleteParameterContract() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "public-api-javadocs/negative.fixture", "ApiNegative.java");

    var violations = new PublicApiJavadocGate().evaluate(context);

    assertEquals(
        java.util.List.of(
            "PUBLIC_API_JAVADOC_NOT_MULTILINE",
            "PUBLIC_API_PARAM_MISSING",
            "PUBLIC_API_JAVADOC_SUMMARY_MISSING",
            "PUBLIC_API_PARAM_MEANING_MISSING",
            "PUBLIC_API_PARAM_RANGE_MISSING"),
        violations.stream().map(QualityViolation::code).toList());
  }
}
