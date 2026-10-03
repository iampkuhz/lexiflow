package io.lexiflow.api.runtime;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

/** 从构建时嵌入的唯一版本资源读取软件身份。 */
public final class SoftwareIdentity {
  private static final String RESOURCE = "/META-INF/lexiflow-version.txt";
  private static final int MAX_RESOURCE_BYTES = 256;
  private static final Pattern VERSION =
      Pattern.compile(
          "(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})\\.(?:0|[1-9][0-9]{0,4})(?:-SNAPSHOT\\.g[a-f0-9]{7,64}(?:\\.dirty\\.[a-f0-9]{12,64})?)?");
  private final String version;

  /**
   * 从受限类路径资源加载并验证版本。
   *
   * @throws IllegalStateException 资源缺失、过长、无效或无法读取时抛出固定错误。
   */
  public SoftwareIdentity() {
    version = parseVersion(readResource());
  }

  private static String readResource() {
    try (InputStream stream = SoftwareIdentity.class.getResourceAsStream(RESOURCE)) {
      if (stream == null) return "";
      var bytes = stream.readNBytes(MAX_RESOURCE_BYTES + 1);
      return bytes.length > MAX_RESOURCE_BYTES ? "" : new String(bytes, StandardCharsets.US_ASCII);
    } catch (IOException exception) {
      // I/O 故障只映射为无效身份；构造器统一拒绝，不传播包含部署路径的异常。
      return "";
    }
  }

  /**
   * 返回构建时嵌入的规范软件版本。
   *
   * @return 经校验的正式或带源码身份的开发软件版本。
   */
  public String version() {
    return version;
  }

  /**
   * 校验正式或开发软件身份，供运行健康检查共享。
   *
   * @param raw 含义：构建资源或运行响应中的软件版本。取值范围：非 null 的正式或开发版本，可带单个结尾换行。
   * @return 经校验且移除单个结尾换行的版本。
   */
  public static String parseVersion(String raw) {
    var value =
        raw.endsWith("\r\n")
            ? raw.substring(0, raw.length() - 2)
            : raw.endsWith("\n") ? raw.substring(0, raw.length() - 1) : raw;
    if (!VERSION.matcher(value).matches()) throw invalidVersion();
    var parts = value.split("-", 2)[0].split("\\.");
    boolean allZero = true;
    for (var part : parts) {
      // 正则已限制为最多五位数字，不存在解析溢出。
      int number = Integer.parseInt(part);
      if (number > 65535) throw invalidVersion();
      allZero &= number == 0;
    }
    if (allZero) throw invalidVersion();
    return value;
  }

  private static IllegalStateException invalidVersion() {
    return new IllegalStateException("LexiFlow software version resource is invalid");
  }
}
