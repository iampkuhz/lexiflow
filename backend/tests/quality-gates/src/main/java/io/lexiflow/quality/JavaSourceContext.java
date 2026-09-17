package io.lexiflow.quality;

import java.util.List;

/**
 * 三条源码规则共享的不可变输入。
 *
 * @param allSources 主源码与测试源码，用于注释语言规则。
 * @param parsedSources 主源码与测试源码的共享解析结果。
 */
public record JavaSourceContext(List<JavaSourceFile> allSources, ParsedJavaSources parsedSources) {

  /** 对文本源码列表做防御性复制。 */
  public JavaSourceContext {
    allSources = List.copyOf(allSources);
  }
}
