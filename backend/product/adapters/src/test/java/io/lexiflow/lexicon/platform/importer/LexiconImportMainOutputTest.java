package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;

class LexiconImportMainOutputTest {
  @Test
  void validateReportUsesChineseLabelsAndOneFieldPerLineWithoutSpaces() throws Exception {
    var input = Files.createTempFile("lexicon-validation", ".csv");
    Files.writeString(
        input,
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            + "reliable,,worthy of reliance,可靠的,,,1,cet6,1,1,,,\n");
    String report;
    try {
      report = captureValidationReport(input, System.out);
    } finally {
      Files.deleteIfExists(input);
    }
    assertTrue(report.startsWith("导入校验结果：PASS\n"), report);
    assertTrue(report.contains("来源行数（source_rows）：1\n"), report);
    assertTrue(report.contains("可导入词条数（entries）：1\n"), report);
    assertTrue(report.contains("归并派生词数（derived_merged）：0\n"), report);
    assertTrue(report.contains("来源文件SHA-256（source_sha256）："), report);
    assertFalse(Pattern.compile("\\s").matcher(report.replace("\n", "")).find(), report);
  }

  // originalOut 是调用者借出的标准输出；这里只恢复，不取得关闭它的所有权。
  private static String captureValidationReport(Path input, PrintStream originalOut)
      throws Exception {
    var output = new ByteArrayOutputStream();
    try (var capture = new PrintStream(output, true, StandardCharsets.UTF_8)) {
      System.setOut(capture);
      LexiconImportMain.main(new String[] {"validate", "--input", input.toString()});
    } finally {
      System.setOut(originalOut);
    }
    return output.toString(StandardCharsets.UTF_8);
  }
}
