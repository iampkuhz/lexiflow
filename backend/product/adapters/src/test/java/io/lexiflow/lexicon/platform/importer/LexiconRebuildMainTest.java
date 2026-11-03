package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.StringReader;
import org.junit.jupiter.api.Test;

/** 验证重建确认必须与实际 SQLite 文件路径精确一致。 */
class LexiconRebuildMainTest {

  @Test
  void acceptsOnlyExactTargetConfirmation() {
    assertDoesNotThrow(
        () ->
            LexiconRebuildMain.requireConfirmation(
                "/tmp/lexiflow.db", new StringReader("REBUILD /tmp/lexiflow.db\n")));
    assertThrows(
        IllegalStateException.class,
        () ->
            LexiconRebuildMain.requireConfirmation(
                "/tmp/lexiflow.db", new StringReader("REBUILD /tmp/other.db\n")));
    assertThrows(
        IllegalStateException.class,
        () -> LexiconRebuildMain.requireConfirmation("/tmp/lexiflow.db", new StringReader("")));
    assertThrows(
        IllegalStateException.class,
        () -> LexiconRebuildMain.requireConfirmation("/tmp/lexiflow.db", new StringReader("y\n")));
  }

  @Test
  void eofAndBlankInputHaveExplicitSafeFailureMessages() {
    var eof =
        assertThrows(
            IllegalStateException.class,
            () -> LexiconRebuildMain.requireConfirmation("/tmp/lexiflow.db", new StringReader("")));
    assertTrue(eof.getMessage().contains("stdin 已关闭"));
    assertTrue(eof.getMessage().contains("未修改数据库"));
    var blank =
        assertThrows(
            IllegalStateException.class,
            () ->
                LexiconRebuildMain.requireConfirmation("/tmp/lexiflow.db", new StringReader("\n")));
    assertTrue(blank.getMessage().contains("已取消重建"));
    assertTrue(blank.getMessage().contains("未修改数据库"));
  }
}
