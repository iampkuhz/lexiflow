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
import java.util.List;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

/** 本机诊断日志只输出本次处理区间，不声称是客户端整屏合成。 */
class CaptionHintLogTest {
  @Test
  void logsOnlyCurrentAppendTextAndHintWithoutIdentity() {
    var message = recordedLog("reliable");
    assertTrue(message.contains("processedEnglish=\"reliable\""));
    assertTrue(message.contains("processedWithHints=\"reliable(可靠的)\""));
    assertFalse(message.contains("old text"));
    assertFalse(message.contains("secret-topic"));
    assertFalse(message.contains("new-key"));
    assertFalse(message.contains("final="));
  }

  @Test
  void escapesUntrustedTextAsOneLine() {
    var message = recordedLog("reliable\nfake=\"x\"");
    assertFalse(message.contains("\n"));
    assertTrue(message.contains("processedEnglish=\"reliable\\nfake=\\\"x\\\"\""));
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
          new CaptionHintRequest(
              "secret-topic",
              null,
              null,
              new CaptionHintRequest.Snapshot(
                  List.of(
                      new CaptionHintRequest.CaptionGroup(
                          null,
                          null,
                          List.of(
                              new CaptionHintRequest.Segment(
                                  "old-key", "old text ", null, false, 0L),
                              new CaptionHintRequest.Segment(
                                  "new-key", caption, null, true, 0L)))))));
      assertEquals(1, appender.list.size());
      return appender.list.getFirst().getFormattedMessage();
    } finally {
      logger.detachAppender(appender);
      appender.stop();
    }
  }
}
