package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
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

  @Test
  void doesNotTreatVerificationSourcesAsProductApi() throws Exception {
    var path = directory.resolve("verification/quality-gates/src/main/java/example/Probe.java");
    Files.createDirectories(path.getParent());
    var content =
        "package example; public class Probe { /** bad */ public void run(String value) {} }";
    Files.writeString(path, content);
    var source =
        new JavaSourceFile(
            path, JavaSourceDiscovery.normalize(directory.relativize(path)), content);
    var sources = List.of(source);
    var context = new JavaSourceContext(sources, ParsedJavaSources.parse(directory, sources));

    assertTrue(new PublicApiJavadocGate().evaluate(context).isEmpty());
  }
}
