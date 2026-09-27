package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.StringReader;
import org.junit.jupiter.api.Test;

/** 验证重建确认必须与实际数据库及 schema 精确一致。 */
class LexiconRebuildMainTest {

  @Test
  void acceptsOnlyExactTargetConfirmation() {
    assertDoesNotThrow(
        () ->
            LexiconRebuildMain.requireConfirmation(
                "lexiflow", "public", new StringReader("REBUILD lexiflow.public\n")));
    assertThrows(
        IllegalStateException.class,
        () ->
            LexiconRebuildMain.requireConfirmation(
                "lexiflow", "public", new StringReader("REBUILD other.public\n")));
    assertThrows(
        IllegalStateException.class,
        () -> LexiconRebuildMain.requireConfirmation("lexiflow", "public", new StringReader("")));
    assertThrows(
        IllegalStateException.class,
        () ->
            LexiconRebuildMain.requireConfirmation("lexiflow", "public", new StringReader("y\n")));
  }
}
