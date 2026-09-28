package io.lexiflow.enrichment.domain.policy;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** 只在字幕原文中定位精确词形，不承担资格、去重或展示决策。 */
final class CandidateMatcher {
  /**
   * 原文中的一次精确词形命中，偏移为 UTF-16。
   *
   * @param startOffset 含义：命中起点。取值范围：原文 UTF-16 偏移。
   * @param endOffset 含义：命中终点。取值范围：大于起点的原文 UTF-16 偏移。
   */
  record Match(int startOffset, int endOffset) {}

  private CandidateMatcher() {}

  /**
   * 定位候选所有精确词形出现，并尊重完整原文的词边界。
   *
   * @param caption 含义：完整字幕原文。取值范围：非 null。
   * @param startOffset 含义：搜索范围起点。取值范围：UTF-16 偏移且不小于零。
   * @param endOffset 含义：搜索范围终点。取值范围：不小于起点且不超过原文长度。
   * @param surface 含义：待定位词形。取值范围：非空精确字符串。
   * @return 范围内全部符合边界的命中，按原文顺序排列。
   */
  static List<Match> locate(String caption, int startOffset, int endOffset, String surface) {
    var found = new ArrayList<Match>();
    var matcher =
        Pattern.compile(Pattern.quote(surface), Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE)
            .matcher(caption)
            .region(startOffset, endOffset);
    while (matcher.find()) {
      if (isWordBoundary(caption, matcher.start(), matcher.end())) {
        found.add(new Match(matcher.start(), matcher.end()));
      }
    }
    return List.copyOf(found);
  }

  private static boolean isWordBoundary(String text, int startOffset, int endOffset) {
    var before = startOffset == 0 || !isWordCodePoint(text.codePointBefore(startOffset));
    var after = endOffset == text.length() || !isWordCodePoint(text.codePointAt(endOffset));
    return before
        && after
        && isCodePointBoundary(text, startOffset)
        && isCodePointBoundary(text, endOffset);
  }

  private static boolean isCodePointBoundary(String value, int offset) {
    return offset == 0
        || offset == value.length()
        || !(Character.isHighSurrogate(value.charAt(offset - 1))
            && Character.isLowSurrogate(value.charAt(offset)));
  }

  private static boolean isWordCodePoint(int codePoint) {
    return Character.isLetterOrDigit(codePoint)
        || codePoint == '_'
        || switch (Character.getType(codePoint)) {
          case Character.NON_SPACING_MARK,
              Character.COMBINING_SPACING_MARK,
              Character.ENCLOSING_MARK ->
              true;
          default -> false;
        };
  }
}
