package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

/** 仅供单元测试使用、由显式 fixture bytes 派生的生成身份。 */
final class TestGenerationFixture {
  private TestGenerationFixture() {}

  static LexiconImportMetadata.GenerationIdentity identity() {
    String input = hash("lexiflow-test-source-fixture-v1");
    String code = hash("lexiflow-test-code-fixture-v1");
    String source = hash("{\"inputSha256\":\"" + input + "\",\"upstream\":{}}");
    String config = "{\"licenseId\":\"MIT\",\"sourceId\":\"ecdict-stardict\"}";
    String version =
        hash(
            "{\"codeVersion\":\""
                + code
                + "\",\"config\":"
                + config
                + ",\"sourceDigest\":\""
                + source
                + "\"}");
    return new LexiconImportMetadata.GenerationIdentity(
        code,
        source,
        version,
        input,
        config,
        "{\"inputSha256\":\"" + input + "\",\"upstream\":{}}");
  }

  private static String hash(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (java.security.NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }
}
