package io.lexiflow.enrichment.domain.policy;

import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;

/** 消费冻结发布决定与短释安全性；不清洗或重写发布内容。 */
final class PublishedCandidateEligibility {
  private static final int MAX_GLOSS_CODE_POINTS = 24;

  private PublishedCandidateEligibility() {}

  /**
   * 判断候选是否可显示；不安全候选仍由调用方保留用于歧义处理。
   *
   * @param candidate 含义：已发布词库候选。取值范围：非 null。
   * @return 仅发布 HINT 且短释符合安全规则时为 true。
   */
  static boolean isDisplayable(LexiconHintCandidate candidate) {
    return candidate.finalAction() == LexiconHintAction.HINT && isSafeGloss(candidate.finalGloss());
  }

  private static boolean isSafeGloss(String gloss) {
    var codePointCount = gloss.codePointCount(0, gloss.length());
    if (codePointCount < 1 || codePointCount > MAX_GLOSS_CODE_POINTS) return false;
    var containsHan = false;
    for (var offset = 0; offset < gloss.length(); ) {
      var codePoint = gloss.codePointAt(offset);
      if (!isAllowedGlossCodePoint(codePoint)) return false;
      containsHan |= Character.UnicodeScript.of(codePoint) == Character.UnicodeScript.HAN;
      offset += Character.charCount(codePoint);
    }
    return containsHan;
  }

  private static boolean isAllowedGlossCodePoint(int codePoint) {
    if (Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint)) return false;
    return switch (Character.getType(codePoint)) {
      case Character.CONTROL,
          Character.FORMAT,
          Character.SURROGATE,
          Character.PRIVATE_USE,
          Character.UNASSIGNED,
          Character.CONNECTOR_PUNCTUATION,
          Character.DASH_PUNCTUATION,
          Character.START_PUNCTUATION,
          Character.END_PUNCTUATION,
          Character.INITIAL_QUOTE_PUNCTUATION,
          Character.FINAL_QUOTE_PUNCTUATION,
          Character.OTHER_PUNCTUATION,
          Character.MATH_SYMBOL,
          Character.CURRENCY_SYMBOL,
          Character.MODIFIER_SYMBOL,
          Character.OTHER_SYMBOL ->
          false;
      default -> !isMarkupDelimiter(codePoint);
    };
  }

  private static boolean isMarkupDelimiter(int codePoint) {
    return codePoint == '<' || codePoint == '>' || codePoint == '&' || codePoint == '`';
  }
}
