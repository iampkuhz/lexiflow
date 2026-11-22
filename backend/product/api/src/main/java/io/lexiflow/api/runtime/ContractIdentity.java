package io.lexiflow.api.runtime;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

/** 构建嵌入的兼容号，不接受环境变量覆盖。 */
public final class ContractIdentity {
  private static final Pattern LOCK =
      Pattern.compile(
          "\\{\\s*\"extension-api\"\\s*:\\s*\\{\\s*\"version\"\\s*:\\s*([1-9][0-9]{0,8})\\s*,\\s*"
              + "\"sha256\"\\s*:\\s*\"[a-f0-9]{64}\"\\s*\\}\\s*,\\s*"
              + "\"sqlite-storage\"\\s*:\\s*\\{\\s*\"version\"\\s*:\\s*[1-9][0-9]{0,8}\\s*,\\s*"
              + "\"sha256\"\\s*:\\s*\"([a-f0-9]{64})\"\\s*\\}\\s*\\}\\s*");
  private static final String[] IDENTITY = read();

  private ContractIdentity() {}

  /** 返回当前 API 合同代号。 */
  public static String apiContract() {
    return "caption-hints.v" + IDENTITY[0];
  }

  /** 返回与打包 SQLite 合同绑定的结构摘要。 */
  public static String storageDigest() {
    return IDENTITY[1];
  }

  private static String[] read() {
    try (var resource =
        ContractIdentity.class.getResourceAsStream("/META-INF/lexiflow-contracts.json")) {
      if (resource != null) {
        var bytes = resource.readNBytes(2049);
        var match = LOCK.matcher(new String(bytes, StandardCharsets.US_ASCII));
        if (bytes.length < 2049 && match.matches())
          return new String[] {match.group(1), match.group(2)};
      }
    } catch (IOException e) {
      throw new IllegalStateException("invalid contract lock resource", e);
    }
    throw new IllegalStateException("invalid contract lock resource");
  }
}
