package io.lexiflow.api.hints;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

/** 本机诊断日志只输出耗时、英文和与扩展一致的最终提示字符串。 */
class CaptionHintLogTest {
  private static final UUID CONTENT_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");
  private static final String SEGMENT_ID = "a".repeat(64);

  @Test
  void logsEnglishAndFinalInlineHintWithoutOtherRequestDetails() {
    var message = recordedLog("We need reliable captions.");

    assertTrue(
        message.matches(
            "hint_result apiMs=[0-9]+[.][0-9]{3} english=\\\"We need reliable captions[.]\\\" final=\\\"We need reliable\\(可靠的\\) captions[.]\\\""));
    assertFalse(message.contains(CONTENT_ID.toString()));
    assertFalse(message.contains("state="));
    assertFalse(message.contains("queryMs="));
    assertFalse(message.contains("rulesMs="));
  }

  @Test
  void logsUnchangedEnglishWhenThereIsNoDisplayableHint() {
    var message = recordedLog("Nothing familiar here.");

    assertTrue(message.contains("english=\"Nothing familiar here.\""));
    assertTrue(message.contains("final=\"Nothing familiar here.\""));
  }

  @Test
  void escapesUntrustedCaptionAsOneLogLine() {
    var message = recordedLog("reliable\nfake=\"x\"");

    assertFalse(message.contains("\n"));
    assertTrue(message.contains("english=\"reliable\\nfake=\\\"x\\\"\""));
    assertTrue(message.contains("final=\"reliable(可靠的)\\nfake=\\\"x\\\"\""));
  }

  private static String recordedLog(String caption) {
    var logger = (Logger) LoggerFactory.getLogger(CaptionHintController.class);
    var appender = new ListAppender<ILoggingEvent>();
    appender.start();
    logger.addAppender(appender);
    try {
      var controller =
          new CaptionHintController(
              new EnrichCaptionUseCase(new BuiltinLexiconCatalog(), new DeterministicHintPolicy()));
      controller.hint(
          new CaptionHintRequest(CONTENT_ID, 1, SEGMENT_ID, caption, 0, caption.length()));
      assertEquals(1, appender.list.size());
      return appender.list.getFirst().getFormattedMessage();
    } finally {
      logger.detachAppender(appender);
      appender.stop();
    }
  }
}
