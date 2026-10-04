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
  void missingParamReportsCompleteRequirementsAndCollectsOtherMissingParameters() throws Exception {
    var path = directory.resolve("product/api/src/main/java/example/Api.java");
    Files.createDirectories(path.getParent());
    var content =
        "package example;\n"
            + "public class Api {\n"
            + "  /** Does work.\n   * @param first missing\n   */\n"
            + "  public void run(String first, String second) {}\n"
            + "}\n";
    Files.writeString(path, content);
    var source =
        new JavaSourceFile(
            path, JavaSourceDiscovery.normalize(directory.relativize(path)), content);
    var sources = List.of(source);
    var context = new JavaSourceContext(sources, ParsedJavaSources.parse(directory, sources));

    var violations = new PublicApiJavadocGate().evaluate(context);

    var missing =
        violations.stream().filter(item -> item.code().equals("PUBLIC_API_PARAM_MISSING")).toList();
    assertEquals(
        List.of("second"),
        missing.stream().map(item -> item.attributes().get("parameter")).toList());
    assertTrue(missing.getFirst().message().contains("含义"));
    assertTrue(missing.getFirst().message().contains("取值范围"));
    assertTrue(missing.getFirst().message().contains("格式"));
    var first =
        violations.stream()
            .filter(item -> item.code().equals("PUBLIC_API_PARAM_MEANING_MISSING"))
            .toList();
    assertEquals(1, first.size());
    assertTrue(
        violations.stream().anyMatch(item -> item.code().equals("PUBLIC_API_PARAM_RANGE_MISSING")));
  }

  @Test
  void absentJavadocReportsSummaryFormatAndEveryParameterTogether() throws Exception {
    var path = directory.resolve("product/api/src/main/java/example/Undocumented.java");
    Files.createDirectories(path.getParent());
    var content =
        "package example; public class Undocumented { public void run(String first, int second) {} }";
    Files.writeString(path, content);
    var sources =
        List.of(
            new JavaSourceFile(
                path, JavaSourceDiscovery.normalize(directory.relativize(path)), content));
    var context = new JavaSourceContext(sources, ParsedJavaSources.parse(directory, sources));
    var violations = new PublicApiJavadocGate().evaluate(context);
    assertEquals(
        List.of(
            "PUBLIC_API_JAVADOC_MISSING", "PUBLIC_API_PARAM_MISSING", "PUBLIC_API_PARAM_MISSING"),
        violations.stream().map(QualityViolation::code).toList());
    assertTrue(violations.getFirst().message().contains("整体职责"));
    assertTrue(
        violations.stream()
            .allMatch(item -> item.message().contains("含义") && item.message().contains("取值范围")));
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
