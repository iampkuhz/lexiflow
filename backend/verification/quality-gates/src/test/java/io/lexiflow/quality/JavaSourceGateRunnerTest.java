package io.lexiflow.quality;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
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
    assertEquals(first, JavaSourceGateRunner.run(directory));
  }

  @Test
  void malformedSourceFailsClosedBeforeEvaluation() throws Exception {
    var path = directory.resolve("product/sample/src/main/java/example/Broken.java");
    Files.createDirectories(path.getParent());
    Files.writeString(path, "package example; class Broken { invalid syntax");
    assertThrows(
        IOException.class,
        () ->
            ParsedJavaSources.parse(
                List.of(new JavaSourceFile(path, "Broken.java", Files.readString(path)))));
    assertThrows(IOException.class, () -> JavaSourceGateRunner.run(directory));
  }
}
