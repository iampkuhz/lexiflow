package io.lexiflow.observability.platform;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

/** 验证应用观察端口映射只输出闭合原因、计数和规则键。 */
final class LexiconEventObserverTest {
  @Test
  void mapsImportAndCachePortsAndDropsUnknownRuleText() throws Exception {
    var line = new AtomicReference<String>();
    var observer =
        new LexiconEventObserver(new StructuredEventLogger((level, json) -> line.set(json)));
    observer.onEvent(
        new LexiconImportObserver.Event(
            LexiconImportObserver.Step.PUBLISH,
            LexiconImportObserver.Phase.COMPLETED,
            LexiconImportObserver.Reason.OK,
            2,
            Map.of(
                LexiconImportObserver.Count.INPUT_ROWS,
                5L,
                LexiconImportObserver.Count.PREPARED_ROWS,
                3L,
                LexiconImportObserver.Count.LOOKUP_ROWS,
                7L),
            Map.of("non_ascii_lemma", 1L, "private synthetic lemma", 999L),
            8L,
            true));
    assertTrue(line.get().contains("lexicon.import.completed"));
    assertTrue(line.get().contains("non_ascii_lemma"));
    assertFalse(line.get().contains("private synthetic lemma"));
    observer.versionChanged(7, 8, 2, 1, 4_000_000L);
    assertTrue(line.get().contains("lexicon.cache.version_changed"));
    assertTrue(line.get().contains("invalidated_positive"));
    assertFalse(line.get().contains("reliable"));
  }
}
