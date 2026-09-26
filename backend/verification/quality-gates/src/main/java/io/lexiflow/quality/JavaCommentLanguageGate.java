package io.lexiflow.quality;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** 检查 Java 注释是否以中文说明为主体，同时允许必要的规范技术术语。 */
public final class JavaCommentLanguageGate implements JavaSourceGate {

  private static final String ID = "java-comment-language";
  private static final Pattern DIRECTIVE =
      Pattern.compile(
          "^(?:SPDX-|Copyright|noinspection|region|endregion|spotless:|formatter:|CHECKSTYLE|PMD|generated)",
          Pattern.CASE_INSENSITIVE);
  private static final Pattern LOW_INFORMATION =
      Pattern.compile("\\b(?:TODO|TBD|FIXME|XXX)\\b|待补充|稍后处理|临时注释", Pattern.CASE_INSENSITIVE);
  private static final Pattern LICENSE_HEADER =
      Pattern.compile("^(?:SPDX-|Copyright)", Pattern.CASE_INSENSITIVE);
  private static final Pattern URL = Pattern.compile("https?://\\S+");
  private static final Pattern HTML = Pattern.compile("</?[A-Za-z][^>]*>");
  private static final Pattern TAG =
      Pattern.compile(
          "\\{@(?:code|link|linkplain|literal|value)\\s+[^}]*}|"
              + "@(?:param|return|throws|exception|since|see|deprecated)\\b");
  private static final Pattern IDENTIFIER =
      Pattern.compile("`[^`]+`|\\b(?:[A-Za-z_$][\\w$]*\\.)+[A-Za-z_$][\\w$]*\\b");
  private static final Pattern LEADING_MARKER = Pattern.compile("^\\s*\\*?\\s?");
  private static final Pattern WHITESPACE = Pattern.compile("\\s+");
  private static final Set<String> CANONICAL_TERMS =
      Set.of(
          "API",
          "ArchUnit",
          "Checkstyle",
          "DocLint",
          "Gradle",
          "HTML",
          "HTTP",
          "JaCoCo",
          "Java",
          "Javadoc",
          "JDK",
          "JSON",
          "JVM",
          "LexiFlow",
          "PMD",
          "SQL",
          "Spotless",
          "Spring",
          "UTF-8",
          "UUID",
          "XML",
          "component",
          "record",
          "source");

  /** 返回注释语言规则标识。 */
  @Override
  public String id() {
    return ID;
  }

  /** 检查共享文本输入中的每段真实源码注释。 */
  @Override
  public List<QualityViolation> evaluate(JavaSourceContext context) {
    var violations = new ArrayList<QualityViolation>();
    for (var source : context.allSources()) {
      for (var comment : JvmCommentLexer.extract(source.text())) {
        inspect(source.relativePath(), comment, violations);
      }
    }
    return violations.stream().sorted().toList();
  }

  private static void inspect(
      String path, JvmCommentLexer.SourceComment comment, List<QualityViolation> violations) {
    var raw = comment.text().strip();
    var firstLine = firstLine(raw);
    if (raw.isEmpty() || LICENSE_HEADER.matcher(firstLine).find()) {
      return;
    }
    if (LOW_INFORMATION.matcher(raw).find()) {
      violations.add(violation(path, comment.line(), "COMMENT_LOW_INFORMATION", "注释包含占位语或低信息模板"));
      return;
    }
    var normalized = normalize(raw);
    var han = countHan(normalized);
    var latin = countLatin(normalized);
    if (han + latin == 0) {
      return;
    }
    var ratio = han / (double) Math.max(1, han + latin);
    var minimumHan = comment.kind() == JvmCommentLexer.CommentKind.LINE ? 2 : 4;
    var minimumRatio = comment.kind() == JvmCommentLexer.CommentKind.LINE ? 0.08 : 0.12;
    if (han < minimumHan || ratio < minimumRatio) {
      violations.add(
          new QualityViolation(
              ID,
              path,
              comment.line(),
              "COMMENT_NOT_CHINESE_DOMINANT",
              "中文不是注释说明主体",
              Map.of(
                  "hanCount",
                  Integer.toString(han),
                  "latinCount",
                  Integer.toString(latin),
                  "ratio",
                  String.format(Locale.ROOT, "%.3f", ratio))));
    }
    if (raw.contains("{@inheritDoc}") && han < 4) {
      violations.add(
          violation(path, comment.line(), "INHERITDOC_WITHOUT_CHINESE", "不能只用 inheritDoc 代替中文说明"));
    }
  }

  private static String normalize(String raw) {
    var lines = new ArrayList<String>();
    for (var line : raw.split("\\R", -1)) {
      var value = LEADING_MARKER.matcher(line).replaceFirst("").strip();
      if (!value.isEmpty() && !DIRECTIVE.matcher(value).find()) {
        lines.add(value);
      }
    }
    var value = String.join(" ", lines);
    value = URL.matcher(value).replaceAll(" ");
    value = HTML.matcher(value).replaceAll(" ");
    value = TAG.matcher(value).replaceAll(" ");
    value = IDENTIFIER.matcher(value).replaceAll(" ");
    var terms =
        CANONICAL_TERMS.stream()
            .sorted(Comparator.comparingInt(String::length).reversed())
            .toList();
    for (var term : terms) {
      value =
          Pattern.compile(
                  "(?<![A-Za-z0-9_])" + Pattern.quote(term) + "(?![A-Za-z0-9_])",
                  Pattern.CASE_INSENSITIVE)
              .matcher(value)
              .replaceAll(" ");
    }
    return WHITESPACE.matcher(value).replaceAll(" ").strip();
  }

  private static String firstLine(String raw) {
    return raw.isEmpty()
        ? ""
        : LEADING_MARKER.matcher(raw.split("\\R", 2)[0]).replaceFirst("").strip();
  }

  private static int countHan(String value) {
    var count = 0;
    for (var index = 0; index < value.length(); index++) {
      var character = value.charAt(index);
      if ((character >= '\u3400' && character <= '\u4dbf')
          || (character >= '\u4e00' && character <= '\u9fff')
          || (character >= '\uf900' && character <= '\ufaff')) {
        count++;
      }
    }
    return count;
  }

  private static int countLatin(String value) {
    var count = 0;
    for (var index = 0; index < value.length(); index++) {
      var character = value.charAt(index);
      if ((character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z')) {
        count++;
      }
    }
    return count;
  }

  private static QualityViolation violation(String path, int line, String code, String message) {
    return new QualityViolation(ID, path, line, code, message, Map.of());
  }
}
