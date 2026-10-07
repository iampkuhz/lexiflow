package io.lexiflow.api.deployment;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.nio.file.Files;
import java.sql.Connection;
import java.sql.Statement;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;

/** 部署请求预检失败仅输出固定结果，不应触及数据库。 */
@ExtendWith(OutputCaptureExtension.class)
class DeploymentDataCommandTest {
  @Test
  void freezesDatabaseObservationInUtcRegardlessOfJvmDefault() throws Exception {
    try (var connection = mock(Connection.class);
        var statement = mock(Statement.class)) {
      when(connection.createStatement()).thenReturn(statement);
      var setup =
          DeploymentDataCommand.class.getDeclaredMethod("setTargetSearchPath", Connection.class);
      setup.setAccessible(true);
      setup.invoke(null, connection);
      verify(statement).execute("SET search_path TO lexiflow_release");
      verify(statement).execute("SET TIME ZONE 'UTC'");
    }
  }

  @Test
  void rejectsArtifactGenerationMismatchBeforeDatabaseConnection(CapturedOutput captured)
      throws Exception {
    var input = Files.createTempFile("deployment-input-", ".csv");
    var request = Files.createTempFile("deployment-request-", ".json");
    try {
      Files.writeString(input, "not a dataset");
      var sha =
          java.util.HexFormat.of()
              .formatHex(
                  java.security.MessageDigest.getInstance("SHA-256")
                      .digest(Files.readAllBytes(input)));
      Files.writeString(
          request,
          """
          {"inputType":"csv","inputPath":"%s","inputSha256":"%s","generation":{
          "codeVersion":"%s","sourceDigest":"%s","generationVersion":"%s","inputSha256":"%s",
          "config":{"sourceId":"ecdict-stardict","licenseId":"MIT"},"upstream":{}}}
          """
              .formatted(input, sha, "0".repeat(64), "1".repeat(64), "2".repeat(64), sha));
      assertEquals(
          1,
          DeploymentDataCommand.run(
              new String[] {"--deployment-data", "inspect", "--request", request.toString()}));
      assertEquals(
          "{\"status\":\"FAIL\",\"reason\":\"REQUEST_OR_DATA_INVALID\"}\n", captured.getOut());
    } finally {
      Files.deleteIfExists(request);
      Files.deleteIfExists(input);
    }
  }
}
