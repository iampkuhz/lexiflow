package io.lexiflow.api.release;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;

/** 管理入口仅输出固定 JSON；参数和异常不得泄露输入。 */
@ExtendWith(OutputCaptureExtension.class)
class ReleaseDatasetCommandTest {
  @Test
  void rejectsMissingExpectedDigestAndSensitiveInputWithoutEcho(CapturedOutput captured) {
    assertEquals(
        1,
        ReleaseDatasetCommand.run(
            new String[] {
              "--release-dataset", "verify", "--package", "/private/sensitive-name.zip"
            }));
    assertEquals("{\"result\":\"FAIL\"}\n", captured.getOut());
    assertEquals(
        1,
        ReleaseDatasetCommand.run(
            new String[] {
              "--release-dataset",
              "initialize",
              "--package",
              "/private/sensitive-name.zip",
              "--expected-sha256",
              "a".repeat(64),
              "--expected-file",
              "/tmp/secret.db"
            }));
    assertEquals("{\"result\":\"FAIL\"}\n{\"result\":\"FAIL\"}\n", captured.getOut());
  }

  @Test
  void createOnlyDatabaseTargetPreservesAFileWonByAnotherWriter(@TempDir Path temp)
      throws Exception {
    Path target = temp.resolve("lexicon.db");
    Files.writeString(target, "owned by another creator");
    assertThrows(
        FileAlreadyExistsException.class, () -> ReleaseDatasetCommand.createExpectedFile(target));
    assertEquals("owned by another creator", Files.readString(target));
  }

  @Test
  void trustedSqlIsOnApiClasspath() {
    assertNotNull(ReleaseDatasetCommand.class.getResource("/META-INF/lexiflow-schema.sql"));
  }
}
