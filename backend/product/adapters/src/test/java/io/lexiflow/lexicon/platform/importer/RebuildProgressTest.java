package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/** 验证阶段、内部步骤和定时状态不会被误认为 Gradle 百分比。 */
class RebuildProgressTest {

  @Test
  void reportsActiveStageAndDetailPeriodically() throws Exception {
    var bytes = new ByteArrayOutputStream();
    try (var output = new PrintStream(bytes, true, StandardCharsets.UTF_8);
        var progress = new RebuildProgress(output, 20)) {
      progress.start(2, "目标检查与确认", "检查目标");
      progress.detail("等待执行人输入确认文本；尚未删除数据");
      Thread.sleep(120);
      progress.complete();
    }
    var report = bytes.toString(StandardCharsets.UTF_8);
    assertTrue(report.contains("[lexiconRebuild 阶段 2/4 目标检查与确认] 开始"), report);
    assertTrue(report.contains("内部步骤：等待执行人输入确认文本；尚未删除数据"), report);
    assertTrue(report.contains("状态更新：仍在执行或等待"), report);
    assertTrue(report.contains("完成；本阶段用时"), report);
    assertFalse(report.contains("%"), report);
  }

  @Test
  void doesNotReportCompletionForInterruptedStage() {
    var bytes = new ByteArrayOutputStream();
    try (var output = new PrintStream(bytes, true, StandardCharsets.UTF_8);
        var progress = new RebuildProgress(output, 1000)) {
      progress.start(4, "全量导入与发布", "事务内批量写入");
    }
    var report = bytes.toString(StandardCharsets.UTF_8);
    assertTrue(report.contains("未完成；本阶段已用时"), report);
    assertFalse(report.contains("完成；本阶段用时"), report);
  }
}
