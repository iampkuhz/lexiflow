package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class JavaSourceGateRunnerTest {

  @TempDir Path directory;

  @Test
  void returnsTypedDeterministicResults() throws Exception {
    var context =
        GateTestSupport.context(
            directory, "java-comment-language/positive.fixture", "CommentPositive.java");

    var first = JavaSourceGateRunner.evaluate(context);
    var second = JavaSourceGateRunner.evaluate(context);

    assertEquals(GateStatus.PASS, first.status());
    assertEquals(4, first.evaluations().size());
    assertEquals(first, second);
    assertEquals(first.toJson(), second.toJson());
  }
}
