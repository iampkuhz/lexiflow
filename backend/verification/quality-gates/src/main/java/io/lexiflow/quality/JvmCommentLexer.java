package io.lexiflow.quality;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** 词法提取 Java 注释，并忽略字符串、字符与 text block 中的注释标记。 */
final class JvmCommentLexer {

  private JvmCommentLexer() {}

  /**
   * 提取行注释、块注释和 Javadoc。
   *
   * @param rawText Java 原始源码。
   * @return 按源码位置排序的注释。
   */
  static List<SourceComment> extract(String rawText) {
    var decoded = translateUnicode(rawText);
    var text = decoded.text();
    var comments = new ArrayList<SourceComment>();
    var state = State.NORMAL;
    var index = 0;
    var start = 0;
    var startLine = 1;
    while (index < text.length()) {
      switch (state) {
        case NORMAL -> {
          if (startsWith(text, index, "\"\"\"")) {
            state = State.TEXT_BLOCK;
            index += 3;
          } else if (text.charAt(index) == '"') {
            state = State.STRING;
            index++;
          } else if (text.charAt(index) == '\'') {
            state = State.CHARACTER;
            index++;
          } else if (startsWith(text, index, "//")) {
            start = index;
            startLine = decoded.lines()[index];
            state = State.LINE_COMMENT;
            index += 2;
          } else if (startsWith(text, index, "/*")) {
            start = index;
            startLine = decoded.lines()[index];
            state = State.BLOCK_COMMENT;
            index += 2;
          } else {
            index++;
          }
        }
        case STRING, CHARACTER -> {
          var delimiter = state == State.STRING ? '"' : '\'';
          if (text.charAt(index) == '\\') {
            index = Math.min(text.length(), index + 2);
          } else {
            if (text.charAt(index) == delimiter) {
              state = State.NORMAL;
            }
            index++;
          }
        }
        case TEXT_BLOCK -> {
          if (text.charAt(index) == '\\') {
            index = Math.min(text.length(), index + 2);
          } else if (startsWith(text, index, "\"\"\"")) {
            state = State.NORMAL;
            index += 3;
          } else {
            index++;
          }
        }
        case LINE_COMMENT -> {
          if (text.charAt(index) == '\n' || text.charAt(index) == '\r') {
            comments.add(
                new SourceComment(startLine, CommentKind.LINE, text.substring(start + 2, index)));
            state = State.NORMAL;
          }
          index++;
        }
        case BLOCK_COMMENT -> {
          if (startsWith(text, index, "*/")) {
            var kind = startsWith(text, start, "/**") ? CommentKind.JAVADOC : CommentKind.BLOCK;
            comments.add(new SourceComment(startLine, kind, text.substring(start + 2, index)));
            state = State.NORMAL;
            index += 2;
          } else {
            index++;
          }
        }
      }
    }
    if (state == State.LINE_COMMENT) {
      comments.add(new SourceComment(startLine, CommentKind.LINE, text.substring(start + 2)));
    }
    return List.copyOf(comments);
  }

  private static boolean startsWith(String text, int offset, String value) {
    return text.regionMatches(offset, value, 0, value.length());
  }

  /** 按 Java Unicode escape 资格规则解码一次，并保留原文件行号。 */
  private static DecodedInput translateUnicode(String raw) {
    var text = new StringBuilder();
    var lines = new int[raw.length()];
    var rawLine = 1;
    var backslashes = 0;
    var previousTranslated = false;
    for (var index = 0; index < raw.length(); ) {
      var character = raw.charAt(index);
      var end = index + 1;
      var translated = false;
      if (character == '\\'
          && (previousTranslated || backslashes % 2 == 0)
          && end < raw.length()
          && raw.charAt(end) == 'u') {
        while (end < raw.length() && raw.charAt(end) == 'u') {
          end++;
        }
        if (end + 4 > raw.length()) {
          throw new IllegalArgumentException("Incomplete Java Unicode escape");
        }
        character = (char) Integer.parseInt(raw.substring(end, end + 4), 16);
        end += 4;
        translated = true;
      }
      lines[text.length()] = rawLine;
      text.append(character);
      for (var cursor = index; cursor < end; cursor++) {
        if (raw.charAt(cursor) == '\r'
            || (raw.charAt(cursor) == '\n' && (cursor == 0 || raw.charAt(cursor - 1) != '\r'))) {
          rawLine++;
        }
      }
      backslashes = character == '\\' ? backslashes + 1 : 0;
      previousTranslated = translated;
      index = end;
    }
    return new DecodedInput(text.toString(), Arrays.copyOf(lines, text.length()));
  }

  /**
   * 已解码的词法输入及原文件位置映射。
   *
   * @param text 解码后的词法文本。
   * @param lines 每个字符在原文件中的行号。
   */
  private record DecodedInput(String text, int[] lines) {}

  /** Java 源码注释类型。 */
  enum CommentKind {
    LINE,
    BLOCK,
    JAVADOC
  }

  /**
   * 一段已定位的源码注释。
   *
   * @param line 起始行号。
   * @param kind 注释类型。
   * @param text 已去掉外围定界符的原文。
   */
  record SourceComment(int line, CommentKind kind, String text) {}

  /** 词法扫描器状态。 */
  private enum State {
    NORMAL,
    STRING,
    CHARACTER,
    TEXT_BLOCK,
    LINE_COMMENT,
    BLOCK_COMMENT
  }
}
