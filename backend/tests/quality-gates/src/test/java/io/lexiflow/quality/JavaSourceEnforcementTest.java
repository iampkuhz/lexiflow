package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class JavaSourceEnforcementTest {

  @TempDir Path directory;

  @Test
  void discoversHandwrittenSourcesInGeneratedAndBuildPackages() throws Exception {
    for (var name : List.of("generated", "gen", "build", ".gradle")) {
      write(
          "sample/src/main/java/probe/" + name + "/Bypass.java",
          "package probe; /** English documentation bypass attempt. */ class Bypass {}\n");
    }
    var report = JavaSourceGateRunner.run(directory);
    assertEquals(4, report.sourceCount());
    assertEquals(GateStatus.FAIL, report.status());
  }

  @Test
  void rejectsAllPmdSuppressionFormsInMainAndTestSources() throws Exception {
    var values =
        List.of(
            "\"PMD\"",
            "\"PMD.CloseResource\"",
            "\"unused\"",
            "\"all\"",
            "{\"unchecked\", \"PMD\"}",
            "\"P\" + \"MD\"",
            "UNKNOWN");
    for (var sourceSet : List.of("main", "test")) {
      for (var index = 0; index < values.size(); index++) {
        write(
            "sample/src/" + sourceSet + "/java/probe/Case" + index + ".java",
            "package probe; /** 中文测试说明。 */ @SuppressWarnings("
                + values.get(index)
                + ") class Case"
                + index
                + " {}\n");
      }
    }
    var all = JavaSourceDiscovery.discover(directory);
    var context = new JavaSourceContext(all, ParsedJavaSources.parse(directory, all));
    assertEquals(values.size() * 2, new NoPmdSuppressionsGate().evaluate(context).size());
  }

  @Test
  void rejectsLineMarkersButAcceptsMarkersInsideStringsAndBlockDocumentation() throws Exception {
    write(
        "sample/src/test/java/probe/Example.java",
        """
        package probe;
        /** 中文测试说明，文档中的 NOPMD 术语不会压制规则。 */
        class Example {
          String text = "// NOPMD";
          int value; // NOPMD 中文抑制说明
        }
        """);
    var all = JavaSourceDiscovery.discover(directory);
    var context = new JavaSourceContext(all, ParsedJavaSources.parse(directory, all));
    var violations = new NoPmdSuppressionsGate().evaluate(context);
    assertEquals(1, violations.size());
    assertEquals("NOPMD", violations.getFirst().attributes().get("pmdRule"));
  }

  @Test
  void checksExplanationsFollowingDirectiveLines() throws Exception {
    write(
        "sample/src/main/java/probe/Example.java",
        """
        package probe;
        /* formatter:off
         * This English explanatory paragraph must still be checked.
         */
        class Example {}
        """);
    var report = JavaSourceGateRunner.run(directory);
    assertEquals(GateStatus.FAIL, report.status());
  }

  @Test
  void acceptsDirectiveOnlyAndLicenseComments() throws Exception {
    write(
        "sample/src/main/java/probe/Example.java",
        """
        /* Copyright Example
         * Permission is hereby granted to use this software.
         */
        package probe;
        // formatter:off
        /** 中文测试说明。 */
        class Example {}
        """);
    assertEquals(GateStatus.PASS, JavaSourceGateRunner.run(directory).status());
  }

  @Test
  void ignoresBuildOutputsOutsideSourceSets() throws Exception {
    write("sample/build/generated/src/main/java/probe/Excluded.java", "invalid syntax");
    write(
        "sample/src/main/java/probe/Example.java",
        "package probe; /** 中文测试说明。 */ class Example {}\n");
    var report = JavaSourceGateRunner.run(directory);
    assertEquals(1, report.sourceCount());
    assertTrue(report.evaluations().stream().allMatch(item -> item.status() == GateStatus.PASS));
  }

  @Test
  void checksRecordComponentsAfterAnnotationArrayBraces() throws Exception {
    write(
        "sample/src/main/java/probe/Example.java",
        """
        package probe;
        /** 中文注解说明。 */ @interface Marker { String[] value(); }
        /** 中文记录说明。
         * @param first 第一参数说明。
         */
        record Example(@Marker({"x"}) String first, String second) {
          /** 中文常量说明。 */ static final String CONSTANT = "value";
        }
        """);
    var report = JavaSourceGateRunner.run(directory);
    var recordResult =
        report.evaluations().stream()
            .filter(item -> item.rule().equals("record-component-javadocs"))
            .findFirst()
            .orElseThrow();
    assertEquals(GateStatus.FAIL, recordResult.status());
    assertEquals(1, recordResult.violations().size());
    assertEquals("second", recordResult.violations().getFirst().attributes().get("component"));
  }

  private void write(String relative, String content) throws Exception {
    var file = directory.resolve(relative);
    Files.createDirectories(file.getParent());
    Files.writeString(file, content);
  }
}
