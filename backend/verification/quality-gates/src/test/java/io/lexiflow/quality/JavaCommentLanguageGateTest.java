package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class JavaCommentLanguageGateTest {

  @TempDir Path directory;

  @Test
  void acceptsChineseCommentsAndIgnoresMarkersInsideStrings() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "java-comment-language/positive.fixture", "CommentPositive.java");

    assertTrue(new JavaCommentLanguageGate().evaluate(context).isEmpty());
  }

  @Test
  void rejectsEnglishDominantCommentsWithStableCode() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "java-comment-language/negative.fixture", "CommentNegative.java");

    var violations = new JavaCommentLanguageGate().evaluate(context);

    assertEquals(1, violations.size());
    assertEquals("COMMENT_NOT_CHINESE_DOMINANT", violations.getFirst().code());
  }
}
