package io.lexiflow.api.release;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
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
              "--expected-database",
              "secret-db",
              "--expected-schema",
              "lexiflow_secret",
              "--expected-schema",
              "lexiflow_secret"
            }));
    assertEquals("{\"result\":\"FAIL\"}\n{\"result\":\"FAIL\"}\n", captured.getOut());
  }

  @Test
  void trustedSqlIsOnApiClasspath() {
    assertNotNull(ReleaseDatasetCommand.class.getResource("/META-INF/lexiflow-schema.sql"));
  }
}
