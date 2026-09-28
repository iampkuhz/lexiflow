package io.lexiflow.lexicon.application.importing.validation;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 依冻结顺序执行有界 StarDict 首候选文本清洗，不访问来源或持久化。 */
public final class StardictGlossCleaner {
  private static final String HAN = "\\p{IsHan}";
  private static final String LABEL = "[\\p{IsHan}A-Za-z]{1,12}";
  private static final Pattern ACRONYM_LEMMA = Pattern.compile("[a-z]{2,8}");
  private static final Pattern ROMAN_NUMERAL = Pattern.compile("[IVXLCDM]+");
  private static final Pattern SHORT_ASCII_SYMBOL = Pattern.compile("[A-Za-z]{1,2}");
  private static final Pattern COMPACT_SEPARATORS = Pattern.compile("[- ]");
  private static final Set<String> ACRONYM_OMIT =
      Set.of("a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "with", "by", "at");
  private static final Pattern SOURCE_LABEL =
      Pattern.compile(
          "^(?:【"
              + LABEL
              + "】 *|\\["
              + LABEL
              + "\\] *|〔"
              + LABEL
              + "〕 *|(?:\\[(?:"
              + HAN
              + "{1,8}(?:[、，,] *"
              + HAN
              + "{1,8}){1,3})\\]|【(?:"
              + HAN
              + "{1,8}(?:[、，,] *"
              + HAN
              + "{1,8}){1,3})】) *)");
  private static final Pattern ENGLISH_EXPANSION =
      Pattern.compile("^(?:\\[=[A-Za-z][A-Za-z0-9 .,'/-]*\\]|\\(=[A-Za-z][A-Za-z0-9 .,'/-]*\\)) *");
  private static final String USAGE = "外|口|俚|方|古|俗|美|英|诗|废";
  private static final String LANGUAGE = "拉|法|德|意|日|西|葡|俄|希|荷|阿拉伯|梵|印地";
  private static final String ANGLE_LABEL = "美俚|美口|英口|英方|主英|非正|非正式|罕|谑|贬|褒|澳|苏格兰|爱尔兰|加拿大|主美|英俚";
  private static final Pattern ANGLE_TAG =
      Pattern.compile(
          "^(?:〈(?:"
              + USAGE
              + "|"
              + LANGUAGE
              + "|"
              + ANGLE_LABEL
              + ")〉 *|<(?:(?:"
              + USAGE
              + "|"
              + LANGUAGE
              + "|"
              + ANGLE_LABEL
              + ")> *)|＜(?:"
              + USAGE
              + "|"
              + LANGUAGE
              + ")＞ *)");
  private static final Pattern PERSON =
      Pattern.compile(
          "^\\(([A-Za-z][A-Za-z .'-]*)\\)人名[；;]\\(([\\p{IsHan}、]{1,24})\\)(?=[\\p{IsHan}])");
  private static final Pattern LEADING_HAN =
      Pattern.compile("^(?:\\(([\\p{IsHan}]{2,24})\\)|（([\\p{IsHan}]{2,24})）) *(?=[\\p{IsHan}])");
  private static final Pattern ACRONYM =
      Pattern.compile("^([A-Za-z][A-Za-z -]*?) +(?=[\\p{IsHan}])");
  private static final Pattern HAN_SPACING = Pattern.compile("(?<=[\\p{IsHan}]) +(?=[\\p{IsHan}])");
  private static final Pattern MIXED_SPACING =
      Pattern.compile("(?<=[\\p{IsHan}]) +(?=[A-Za-z0-9])|(?<=[A-Za-z0-9]) +(?=[\\p{IsHan}])");
  private static final Pattern HAN_HEAD = Pattern.compile("^([\\p{IsHan}]{1,24}) *");
  private static final Pattern MEDICAL_INSERT =
      Pattern.compile("(?<=[\\p{IsHan}])\\[([\\p{IsHan}]{1,2})\\](?=[\\p{IsHan}；;，,、]|$)");
  private static final Pattern BRACKET_CLOSER = Pattern.compile("[)）\\]】]");
  private static final List<Step> STEPS = createSteps();

  private StardictGlossCleaner() {}

  /**
   * 按固定步骤清洗来源释义，每步后重新解析并在首候选安全时立即终止。
   *
   * @param lemma 含义：来源词形，用于核验缩写及人名表头。取值范围：非 null 字符串。
   * @param sourceGloss 含义：不可变原始来源释义。取值范围：非 null 字符串。
   * @return 含义：工作释义、候选及归因规则；无法安全解析时为 unresolved。
   */
  public static Result clean(String lemma, String sourceGloss) {
    Objects.requireNonNull(lemma, "lemma");
    Objects.requireNonNull(sourceGloss, "sourceGloss");
    String working = sourceGloss;
    String first = StardictGlossPreparation.firstCandidate(working);
    if (StardictGlossPreparation.safeGloss(first))
      return new Result(working, first, "existing_safe", List.of());
    var matched = new ArrayList<String>();
    for (var step : STEPS) {
      String next = step.transform().apply(lemma, sourceGloss, working);
      if (next.equals(working)) continue;
      working = next;
      matched.add(step.id());
      first = StardictGlossPreparation.firstCandidate(working);
      if (StardictGlossPreparation.safeGloss(first))
        return new Result(working, first, step.id(), List.copyOf(matched));
    }
    return new Result(
        working,
        StardictGlossPreparation.firstCandidate(working),
        "unresolved",
        List.copyOf(matched));
  }

  private static List<Step> createSteps() {
    return List.of(
        step("source_label", (l, raw, value) -> replaceFirst(SOURCE_LABEL, value, "")),
        step("english_expansion", (l, raw, value) -> replaceFirst(ENGLISH_EXPANSION, value, "")),
        step("angle_tag", (l, raw, value) -> replaceFirst(ANGLE_TAG, value, "")),
        step("person_header", StardictGlossCleaner::personHeader),
        step("leading_han", (l, raw, value) -> replaceFirst(LEADING_HAN, value, "$1$2")),
        step("acronym_expansion", StardictGlossCleaner::acronymExpansion),
        step("han_spacing", (l, raw, value) -> HAN_SPACING.matcher(value).replaceAll("")),
        step("mixed_spacing", (l, raw, value) -> MIXED_SPACING.matcher(value).replaceAll("")),
        step("trailing_parenthesis", (l, raw, value) -> trailingParenthesis(value)),
        step("medical_insert", StardictGlossCleaner::medicalInsert));
  }

  private static String personHeader(String lemma, String raw, String value) {
    Matcher m = PERSON.matcher(value);
    if (!m.find() || !compact(m.group(1)).equals(compact(lemma))) return value;
    return value.substring(m.end());
  }

  private static String acronymExpansion(String lemma, String raw, String value) {
    if (!ACRONYM_LEMMA.matcher(lemma).matches()) return value;
    Matcher m = ACRONYM.matcher(value);
    if (!m.find()) return value;
    String[] words = m.group(1).strip().toLowerCase(Locale.ROOT).split("[ -]+");
    if (java.util.Arrays.stream(words).filter(w -> !ACRONYM_OMIT.contains(w)).count() < 2)
      return value;
    var positions = new java.util.HashSet<Integer>();
    positions.add(0);
    for (String word : words) {
      var next = new java.util.HashSet<Integer>();
      for (int pos : positions) {
        if (ACRONYM_OMIT.contains(word)) next.add(pos);
        if (pos < lemma.length() && lemma.charAt(pos) == word.charAt(0)) next.add(pos + 1);
      }
      positions = next;
    }
    return positions.contains(lemma.length()) ? value.substring(m.end()) : value;
  }

  private static String trailingParenthesis(String value) {
    String first = StardictGlossPreparation.firstCandidate(value);
    Matcher head = HAN_HEAD.matcher(first);
    if (!head.find()) return value;
    String candidate = first;
    int start = head.end();
    if (start >= candidate.length()) return value;
    int open = candidate.charAt(start) == '(' ? start : candidate.charAt(start) == '（' ? start : -1;
    if (open < 0) return value;
    int close = matchingClose(candidate, open);
    if (close != candidate.length() - 1) return value;
    String inside = candidate.substring(open + 1, close).strip();
    if (inside.isEmpty()
        || ROMAN_NUMERAL.matcher(inside).matches()
        || SHORT_ASCII_SYMBOL.matcher(inside).matches()) return value;
    String cleaned =
        candidate.substring(0, head.start(1))
            + candidate.substring(head.start(1), open).stripTrailing();
    return replaceCandidatePrefix(value, candidate, cleaned);
  }

  private static int matchingClose(String text, int start) {
    var expected = new java.util.ArrayDeque<Character>();
    for (int i = start; i < text.length(); i++) {
      char c = text.charAt(i);
      char close =
          switch (c) {
            case '(' -> ')';
            case '（' -> '）';
            case '[' -> ']';
            case '【' -> '】';
            default -> 0;
          };
      if (close != 0) expected.push(close);
      else if (BRACKET_CLOSER.matcher(String.valueOf(c)).matches()) {
        if (expected.isEmpty() || expected.pop() != c) return -1;
        if (expected.isEmpty()) return i;
      }
    }
    return -1;
  }

  private static String medicalInsert(String lemma, String raw, String value) {
    if (!raw.startsWith("[医]")) return value;
    return MEDICAL_INSERT.matcher(value).replaceAll("$1");
  }

  private static String replaceCandidatePrefix(
      String full, String oldCandidate, String newCandidate) {
    int index = full.indexOf(oldCandidate);
    return index < 0
        ? full
        : full.substring(0, index) + newCandidate + full.substring(index + oldCandidate.length());
  }

  private static String compact(String value) {
    return COMPACT_SEPARATORS.matcher(value.toLowerCase(Locale.ROOT)).replaceAll("");
  }

  private static String replaceFirst(Pattern pattern, String value, String replacement) {
    return pattern.matcher(value).replaceFirst(replacement);
  }

  private static Step step(String id, Transform transform) {
    return new Step(id, transform);
  }

  /**
   * 清洗结果；workingGloss 始终是本次运行文本，不覆盖 sourceGloss。
   *
   * @param workingGloss 规则转换后的整段工作文本，非 null。
   * @param candidate 首义解析结果；未解决时仍保留不安全候选，非 null。
   * @param decisiveRule 决定终态的规则 ID：existing_safe、步骤 ID 或 unresolved。
   * @param matchedRules 已改变文本的步骤 ID，按执行顺序排列的不可变列表。
   */
  public record Result(
      String workingGloss, String candidate, String decisiveRule, List<String> matchedRules) {
    /** 构造不可变清洗结果。 */
    public Result {
      Objects.requireNonNull(workingGloss);
      Objects.requireNonNull(candidate);
      Objects.requireNonNull(decisiveRule);
      matchedRules = List.copyOf(matchedRules);
    }
  }

  /** 读取原始范围证据并返回工作文本的单步转换。 */
  @FunctionalInterface
  private interface Transform {
    String apply(String lemma, String raw, String value);
  }

  /**
   * 一个有稳定归因标识的有界转换。
   *
   * @param id 用于终态和轨迹归因的规则标识。
   * @param transform 不修改原始来源的纯文本转换。
   */
  private record Step(String id, Transform transform) {}
}
