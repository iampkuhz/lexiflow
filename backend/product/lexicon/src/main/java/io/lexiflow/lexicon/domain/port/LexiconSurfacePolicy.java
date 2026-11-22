package io.lexiflow.lexicon.domain.port;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** 词形资格与查询窗口的共享纯合同，不读取来源释义、词频或观看状态。 */
public final class LexiconSurfacePolicy {
  /** 当前观看查询最多枚举的连续词元数。 */
  public static final int MAX_PHRASE_TOKENS = 3;

  private static final Pattern QUERY_WORD =
      Pattern.compile("[\\p{IsAlphabetic}]+(?:['-][\\p{IsAlphabetic}]+)*");
  private static final Set<String> STARTS = Set.of("a", "an", "the", "not");
  private static final Set<String> ENDS =
      Set.of("to", "of", "for", "by", "with", "in", "on", "at", "from");
  private static final Set<String> TIMES = Set.of("today", "yesterday", "tomorrow");
  private static final Set<String> FUNCTIONS =
      Set.of(
          ("a am an and are as at be been being but by can could did do does even for from had has have "
                  + "he her hers herself him himself his i if in is it its itself may me might mine must my myself "
                  + "not of on or our ours ourselves shall she should that the their theirs them themselves these "
                  + "they this those to us was we were what when which who whom whose will with would you your "
                  + "yours yourself yourselves")
              .split(" "));

  private LexiconSurfacePolicy() {}

  /**
   * 命中低信息形状且实义词不足两个才阻断；不对全部短语设词数准入门槛。
   *
   * @param lemma 含义：已规范化词形。取值范围：非 null。
   * @return 是否应以低信息短语阻断。
   */
  public static boolean lowInformationPhrase(String lemma) {
    Objects.requireNonNull(lemma, "lemma");
    if (!lemma.contains(" ")) return false;
    var tokens = lemma.split(" ");
    if (tokens.length < 2) return false;
    var first = tokens[0];
    var last = tokens[tokens.length - 1];
    var contentCount = Arrays.stream(tokens).filter(token -> !FUNCTIONS.contains(token)).count();
    return contentCount < 2
        && (STARTS.contains(first)
            || ENDS.contains(last)
            || Arrays.stream(tokens).allMatch(FUNCTIONS::contains)
            || (first.equals("on") && TIMES.contains(last)));
  }

  /**
   * 按观看查询的字符边界生成词元；不修改来源 lemma 身份。
   *
   * @param value 含义：字幕或表面形式。取值范围：非 null。
   * @return 规范化词元；无可查询字符时为空。
   */
  public static List<String> queryTokens(String value) {
    var matcher = QUERY_WORD.matcher(normalizeTypography(value.toLowerCase(Locale.ROOT)));
    var tokens = new java.util.ArrayList<String>();
    while (matcher.find()) tokens.add(matcher.group());
    return List.copyOf(tokens);
  }

  /**
   * 仅将等长英文排印连接符规范为查表字符，保留原文 UTF-16 偏移。
   *
   * @param value 含义：原始英文表面。取值范围：非 null。
   * @return 等长规范化表面；不改变空格、破折号或词条身份。
   */
  public static String normalizeTypography(String value) {
    return value
        .replace('\u2019', '\'')
        .replace('\u2018', '\'')
        .replace('\u2010', '-')
        .replace('\u2011', '-')
        .replace('\uff0d', '-');
  }

  /**
   * 检查查询资格：单词至少三个字母，完整短语仍按查询窗口独立判断。
   *
   * @param value 含义：词形表面。取值范围：非 null。
   * @return 是否包含一至三个查询词元且单词达到最小字母数。
   */
  public static boolean withinQueryWindow(String value) {
    // 导入资格与完整词查询分开：排印修复不扩大旧资料准入，也不丢失已发布短复合词。
    var normalized =
        value.toLowerCase(Locale.ROOT).replaceAll("[^\\p{IsAlphabetic}']+", " ").trim();
    var tokens = normalized.isEmpty() ? List.<String>of() : List.of(normalized.split(" +"));
    int count = tokens.size();
    if (count == 1 && tokens.getFirst().codePoints().filter(Character::isAlphabetic).count() < 3)
      return false;
    return count > 0 && count <= MAX_PHRASE_TOKENS;
  }
}
