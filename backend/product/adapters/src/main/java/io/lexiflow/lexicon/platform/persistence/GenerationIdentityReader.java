package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.regex.Pattern;

/** 读取构建嵌入的生成器身份，并为已校验来源字节构造生成元数据。 */
public final class GenerationIdentityReader {
  private static final Pattern CODE =
      Pattern.compile("\\\"codeVersion\\\"\\s*:\\s*\\\"([0-9a-f]{64})\\\"");

  private GenerationIdentityReader() {}

  /**
   * 读取执行期资源提供的代码版本，缺失时拒绝发布。
   *
   * @return 已嵌入的生成代码摘要。
   */
  public static String embeddedCodeVersion() {
    try (var input =
        GenerationIdentityReader.class.getResourceAsStream("/META-INF/lexiflow-generation.json")) {
      if (input == null)
        throw new IllegalStateException("embedded generation identity unavailable");
      byte[] bytes = input.readAllBytes();
      var matcher = CODE.matcher(new String(bytes, StandardCharsets.UTF_8));
      if (!matcher.find()) throw new IllegalStateException("embedded generation identity invalid");
      return matcher.group(1);
    } catch (IOException failure) {
      throw new IllegalStateException("embedded generation identity unavailable", failure);
    }
  }

  /**
   * upstream 为空的开发期 CSV 输入以实际文件摘要建立身份。
   *
   * @param inputSha256 含义：已核验原始 CSV 字节摘要。取值范围：64 位小写十六进制。
   */
  public static LexiconImportMetadata.GenerationIdentity forInput(String inputSha256) {
    if (inputSha256 == null || !inputSha256.matches("[0-9a-f]{64}"))
      throw new IllegalArgumentException("invalid source digest");
    String code = embeddedCodeVersion();
    String source = digest("{\"inputSha256\":\"" + inputSha256 + "\",\"upstream\":{}}");
    String config = "{\"licenseId\":\"MIT\",\"sourceId\":\"ecdict-stardict\"}";
    String version =
        digest(
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
        inputSha256,
        config,
        "{\"inputSha256\":\"" + inputSha256 + "\",\"upstream\":{}}");
  }

  private static String digest(String text) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8)));
    } catch (java.security.NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }
}
