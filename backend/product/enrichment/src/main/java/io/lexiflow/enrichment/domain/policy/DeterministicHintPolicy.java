package io.lexiflow.enrichment.domain.policy;

import io.lexiflow.enrichment.domain.model.AnnotationHint;
import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.CaptionHintResult;
import io.lexiflow.enrichment.domain.model.HintState;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** 根据公开词汇材料生成确定性提示，绝不调用模型或建立伪造的 pending 工作。 */
public final class DeterministicHintPolicy {
  private static final int MAX_HINTS_PER_CAPTION = 3;
  private static final int MAX_GLOSS_CODE_POINTS = 24;
  private static final Set<String> LOW_INFORMATION_STARTS = Set.of("a", "an", "the", "not");
  private static final Set<String> INCOMPLETE_ENDS =
      Set.of("to", "of", "for", "by", "with", "in", "on", "at", "from");
  private static final Set<String> TIME_ADVERBS = Set.of("today", "yesterday", "tomorrow");
  private static final Set<String> FUNCTION_ONLY_TOKENS =
      Set.of(
          "a", "an", "the", "not", "to", "be", "even", "when", "if", "as", "at", "in", "on", "for",
          "of", "by", "and", "or", "but");
  private static final Comparator<CandidateMatch> MATCH_PRIORITY =
      Comparator.comparingInt(CandidateMatch::valueTier)
          .reversed()
          .thenComparing(Comparator.comparingInt(CandidateMatch::finalPriority).reversed())
          .thenComparing(Comparator.comparingInt(CandidateMatch::complexListCount).reversed())
          .thenComparing(Comparator.comparingInt(CandidateMatch::length).reversed())
          .thenComparingInt(CandidateMatch::startOffset)
          .thenComparingInt(CandidateMatch::endOffset)
          .thenComparing(CandidateMatch::entryId)
          .thenComparingLong(CandidateMatch::lexiconVersion)
          .thenComparing(
              CandidateMatch::chineseGloss, Comparator.nullsFirst(Comparator.naturalOrder()));

  /**
   * 在字幕目标范围内定位词汇候选，并返回可直接显示的中文提示。
   *
   * @param context 含义：已验证的字幕上下文。取值范围：非空，区间必须在字幕文本内。
   * @param candidates 含义：Lexicon 提供的版本化候选。取值范围：非空，可为空集合。
   * @return 有提示时为 READY，否则为 NO_PENDING。
   */
  public CaptionHintResult evaluate(CaptionContext context, List<LexiconHintCandidate> candidates) {
    Objects.requireNonNull(context, "context");
    Objects.requireNonNull(candidates, "candidates");
    if (candidates.stream().anyMatch(Objects::isNull)) {
      return noHints(context);
    }
    if (candidates.stream().map(LexiconHintCandidate::lexiconVersion).distinct().limit(2).count()
        > 1) {
      return noHints(context);
    }

    var matches = new ArrayList<CandidateMatch>();
    for (var candidate : candidates) {
      matches.addAll(locate(context, candidate));
    }
    var ambiguousRanges = ambiguousRanges(matches);
    var selected = select(matches, ambiguousRanges);
    if (selected.isEmpty()) {
      return noHints(context);
    }
    return new CaptionHintResult(context.caption(), HintState.READY, selected);
  }

  private static CaptionHintResult noHints(CaptionContext context) {
    return new CaptionHintResult(context.caption(), HintState.NO_PENDING, List.of());
  }

  private static List<CandidateMatch> locate(
      CaptionContext context, LexiconHintCandidate candidate) {
    var matches = new ArrayList<CandidateMatch>();
    var eligible =
        candidate.finalAction() == LexiconHintAction.HINT && !lowInformationPhrase(candidate);
    var qualified =
        eligible && reliableChineseGloss(candidate.finalGloss()) ? candidate.finalGloss() : null;
    var valueTier =
        candidate.entryKind() == LexiconEntryKind.WORD && candidate.frequencyZipf() > 0 ? 1 : 0;
    var surface = candidate.normalizedForm();
    {
      var matcher =
          Pattern.compile(Pattern.quote(surface), Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE)
              .matcher(context.caption())
              .region(context.startOffset(), context.endOffset());
      while (matcher.find()) {
        if (isWordBoundary(context.caption(), matcher.start(), matcher.end())) {
          matches.add(
              new CandidateMatch(
                  matcher.start(),
                  matcher.end(),
                  candidate.entryId().toString(),
                  qualified == null ? null : candidate.senseId().toString(),
                  candidate.lexiconVersion(),
                  qualified,
                  valueTier,
                  candidate.finalPriority(),
                  candidate.complexListCount()));
        }
      }
    }
    return matches;
  }

  /** 排除会把冠词、否定或悬空介词误当完整词组的来源短语。 */
  private static boolean lowInformationPhrase(LexiconHintCandidate candidate) {
    if (candidate.entryKind() != LexiconEntryKind.PHRASE) return false;
    var tokens = candidate.normalizedForm().split(" ");
    var first = tokens[0];
    var last = tokens[tokens.length - 1];
    return LOW_INFORMATION_STARTS.contains(first)
        || INCOMPLETE_ENDS.contains(last)
        || java.util.Arrays.stream(tokens).allMatch(FUNCTION_ONLY_TOKENS::contains)
        || (first.equals("on") && TIME_ADVERBS.contains(last));
  }

  private static Set<Range> ambiguousRanges(List<CandidateMatch> matches) {
    var entryIdsByRange = new java.util.HashMap<Range, Set<String>>();
    for (var match : matches) {
      entryIdsByRange
          .computeIfAbsent(
              new Range(match.startOffset(), match.endOffset()), ignored -> new HashSet<>())
          .add(match.entryId());
    }
    return entryIdsByRange.entrySet().stream()
        .filter(entry -> entry.getValue().size() > 1)
        .map(Map.Entry::getKey)
        .collect(java.util.stream.Collectors.toUnmodifiableSet());
  }

  private static List<AnnotationHint> select(
      List<CandidateMatch> matches, Set<Range> ambiguousRanges) {
    var selected = new ArrayList<CandidateMatch>();
    var selectedEntryIds = new HashSet<String>();
    matches.stream()
        .filter(CandidateMatch::isDisplayable)
        .filter(
            match -> !ambiguousRanges.contains(new Range(match.startOffset(), match.endOffset())))
        .sorted(MATCH_PRIORITY)
        .forEach(
            match -> {
              if (selected.size() < MAX_HINTS_PER_CAPTION
                  && !selectedEntryIds.contains(match.entryId())
                  && selected.stream().noneMatch(existing -> overlaps(existing, match))) {
                selectedEntryIds.add(match.entryId());
                selected.add(match);
              }
            });
    return selected.stream()
        .sorted(Comparator.comparingInt(CandidateMatch::startOffset))
        .map(
            match ->
                new AnnotationHint(
                    match.startOffset(),
                    match.endOffset(),
                    match.entryId(),
                    match.senseId(),
                    match.lexiconVersion(),
                    match.chineseGloss()))
        .toList();
  }

  private static boolean overlaps(CandidateMatch left, CandidateMatch right) {
    return left.startOffset() < right.endOffset() && right.startOffset() < left.endOffset();
  }

  private static boolean reliableChineseGloss(String gloss) {
    var codePointCount = gloss.codePointCount(0, gloss.length());
    if (codePointCount < 1 || codePointCount > MAX_GLOSS_CODE_POINTS) {
      return false;
    }
    var containsHan = false;
    for (var offset = 0; offset < gloss.length(); ) {
      var codePoint = gloss.codePointAt(offset);
      if (!isAllowedGlossCodePoint(codePoint)) {
        return false;
      }
      containsHan |= Character.UnicodeScript.of(codePoint) == Character.UnicodeScript.HAN;
      offset += Character.charCount(codePoint);
    }
    return containsHan;
  }

  private static boolean isAllowedGlossCodePoint(int codePoint) {
    if (Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint)) {
      return false;
    }
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

  /**
   * 当前原文中的候选区间，用于识别同表面歧义。
   *
   * @param startOffset 含义：起始 UTF-16 偏移。取值范围：非负整数。
   * @param endOffset 含义：终止 UTF-16 偏移。取值范围：大于起始偏移且不超过原文长度。
   */
  private record Range(int startOffset, int endOffset) {}

  /**
   * 已定位的发布资料候选，保留不安全候选以避免用过滤掩盖歧义。
   *
   * @param startOffset 含义：起始 UTF-16 偏移。取值范围：非负整数。
   * @param endOffset 含义：终止 UTF-16 偏移。取值范围：大于起始偏移且不超过原文长度。
   * @param entryId 含义：来源词条身份。取值范围：非空 UUID 字符串。
   * @param senseId 含义：来源义项身份。取值范围：可空，空值表示未通过单义资格校验。
   * @param lexiconVersion 含义：来源发布版本。取值范围：正整数。
   * @param chineseGloss 含义：通过资格校验的中文表达。取值范围：可空，空值表示不可显示。
   * @param valueTier 有来源排名的非基础单词优先于未排名短语。
   * @param finalPriority 导入时冻结的非个人化最终提示优先级。
   * @param complexListCount 独立复杂词表证据数量。
   */
  private record CandidateMatch(
      int startOffset,
      int endOffset,
      String entryId,
      String senseId,
      long lexiconVersion,
      String chineseGloss,
      int valueTier,
      int finalPriority,
      int complexListCount) {

    private int length() {
      return endOffset - startOffset;
    }

    private boolean isDisplayable() {
      return senseId != null && chineseGloss != null;
    }
  }
}
