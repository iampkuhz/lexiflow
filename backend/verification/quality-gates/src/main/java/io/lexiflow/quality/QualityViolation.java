package io.lexiflow.quality;

import java.util.Collections;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;

/**
 * 一条稳定、可排序的源码质量违规。
 *
 * @param rule 规则标识。
 * @param path 后端根目录相对路径。
 * @param line 一开始的行号。
 * @param code 稳定诊断代码。
 * @param message 中文诊断说明。
 * @param attributes 稳定排序的诊断属性。
 */
public record QualityViolation(
    String rule, String path, int line, String code, String message, Map<String, String> attributes)
    implements Comparable<QualityViolation> {

  /** 校验字段并冻结属性顺序。 */
  public QualityViolation {
    Objects.requireNonNull(rule, "rule");
    Objects.requireNonNull(path, "path");
    Objects.requireNonNull(code, "code");
    Objects.requireNonNull(message, "message");
    if (rule.isBlank() || code.isBlank() || message.isBlank() || line < 1) {
      throw new IllegalArgumentException("quality violation fields must be valid");
    }
    attributes =
        Collections.unmodifiableMap(new TreeMap<>(attributes == null ? Map.of() : attributes));
  }

  /** 按路径、行号、规则和代码生成确定性顺序。 */
  @Override
  public int compareTo(QualityViolation other) {
    var byPath = path.compareTo(other.path);
    if (byPath != 0) {
      return byPath;
    }
    var byLine = Integer.compare(line, other.line);
    if (byLine != 0) {
      return byLine;
    }
    var byRule = rule.compareTo(other.rule);
    return byRule != 0 ? byRule : code.compareTo(other.code);
  }
}
