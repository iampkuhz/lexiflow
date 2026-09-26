package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class JvmCommentLexerTest {

  @TempDir Path directory;

  @Test
  void rejectsCommentsHiddenBehindUnicodeDelimiters() throws Exception {
    var text =
        "package probe; "
            + unicode("002f")
            + unicode("002a")
            + unicode("002a")
            + " English comment bypass. "
            + unicode("002a")
            + unicode("002f")
            + " class Example {}";
    assertEquals(GateStatus.FAIL, run(text).status());
  }

  @Test
  void rejectsUnicodeEscapedPmdLineMarkers() throws Exception {
    var text =
        "package probe; /** 中文测试说明。 */ class Example { int value; // NO"
            + unicode("0050")
            + "MD 中文抑制说明\n}";
    var report = run(text);
    assertTrue(
        report.evaluations().stream()
            .anyMatch(
                item ->
                    item.rule().equals("no-pmd-suppressions") && item.status() == GateStatus.FAIL));
  }

  @Test
  void ignoresEscapedTripleQuotesInsideTextBlocks() throws Exception {
    var text =
        "package probe; /** 中文测试说明。 */ class Example { String value = \"\"\"\n"
            + "\\\"\"\" // NOPMD\n\"\"\"; }";
    assertEquals(GateStatus.PASS, run(text).status());
  }

  @Test
  void preservesOriginalLinesAfterEscapedNewlines() {
    var comments =
        JvmCommentLexer.extract("class Example {} " + unicode("000a") + "// 中文说明\n// 下一行说明");
    assertEquals(2, comments.size());
    assertEquals(1, comments.getFirst().line());
    assertEquals(2, comments.getLast().line());
  }

  @Test
  void supportsCrLfAndCrOnlyLineTerminators() {
    var comments = JvmCommentLexer.extract("// 第一行说明\r\n// 第二行说明\r// 第三行说明");
    assertEquals(3, comments.size());
    assertEquals(3, comments.getLast().line());
  }

  @Test
  void doesNotTranslateIneligibleBackslashesInsideStrings() throws Exception {
    var text =
        "package probe; /** 中文测试说明。 */ class Example { String value = \""
            + "\\"
            + unicode("002f")
            + "\\"
            + unicode("002f")
            + " NOPMD\"; }";
    assertEquals(GateStatus.PASS, run(text).status());
  }

  private GateReport run(String text) throws Exception {
    var file = directory.resolve("sample/src/main/java/probe/Example.java");
    Files.createDirectories(file.getParent());
    Files.writeString(file, text);
    return JavaSourceGateRunner.run(directory);
  }

  private static String unicode(String digits) {
    return "\\" + "u" + digits;
  }
}
