package io.lexiflow.lexicon.application.importing.policy;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.PreparedHint;
import io.lexiflow.lexicon.application.importing.validation.StardictGlossCleaner;
import io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

/** 离线按固定规则准备候选或分流；不删来源、不调用模型、不在观看时解析释义。 */
public final class HintPreparation {
  private static final Pattern SHORT_LEMMA = Pattern.compile("[a-z]{2,8}");
  private static final Pattern EXPANSION = Pattern.compile("^\\[=[A-Za-z][A-Za-z0-9 .,'/-]*\\]");
  private static final Pattern HAN = Pattern.compile("\\p{IsHan}");
  private static final Pattern CHEMICAL_CHARS = Pattern.compile("[酸酯酶醇醛酮胺苯烷烯炔苷甙肽菌酰腈甾]");
  private static final Pattern CHEMICAL_NOTATION =
      Pattern.compile(
          "[A-Za-z\\u0370-\\u03ff0-9][-‐‑–—－][\\u3400-\\u9fff]"
              + "|[\\u3400-\\u9fff][-‐‑–—－][A-Za-z\\u0370-\\u03ff0-9]");

  private HintPreparation() {}

  /**
   * 返回准备结果的稳定阻断原因。
   *
   * @param row 含义：已校验的导入行。取值范围：非 null。
   * @return 不能提示时的原因，通过时为 null。
   */
  public static String exclusionReason(LexiconImportRow row) {
    return prepare(row).exclusionReason();
  }

  /**
   * 在构造词义之前执行一次准备，结果由词义和查询投影共同使用。
   *
   * @param row 含义：含完整来源与资格证据的导入行。取值范围：非 null。
   * @return 安全候选或明确阻断结果，保留决定规则和变换轨迹。
   */
  public static PreparedHint prepare(LexiconImportRow row) {
    Objects.requireNonNull(row, "row");
    boolean stardict = row.dictionary().sourceId().equals("ecdict-stardict");
    if (stardict && row.lemma().codePoints().anyMatch(HintPreparation::nonAsciiLetter))
      return excluded(row, "non_ascii_lemma", List.of());
    if (stardict && !hasQueryableSurface(row))
      return excluded(row, "outside_query_window", List.of());
    if (row.basicVocabulary()) return excluded(row, "basic_vocabulary", List.of());
    if (row.allBasicPhrase()) return excluded(row, "all_basic_phrase", List.of());
    if (LexiconSurfacePolicy.lowInformationPhrase(row.lemma()))
      return excluded(row, "low_information_phrase", List.of());
    if (!stardict || row.curatedGloss()) {
      return safeGloss(row.chineseGloss())
          ? ready(
              row, row.chineseGloss(), row.curatedGloss() ? "curated" : "existing_safe", List.of())
          : excluded(row, "unsafe_default_candidate", List.of());
    }
    if (rareExpansion(row)) return excluded(row, "english_heavy_expansion", List.of());
    if (!HAN.matcher(row.sourceGloss()).find()) return excluded(row, "no_han_source", List.of());
    String first = StardictGlossPreparation.firstCandidate(row.sourceGloss());
    if (withoutFrequencyProtection(row)
        && CHEMICAL_CHARS.matcher(first).find()
        && CHEMICAL_NOTATION.matcher(first).find())
      return excluded(row, "specialist_notation", List.of());
    var cleaned = StardictGlossCleaner.clean(row.lemma(), row.sourceGloss());
    if (safeGloss(cleaned.candidate()))
      return ready(row, cleaned.candidate(), cleaned.decisiveRule(), cleaned.matchedRules());
    String reason =
        cleaned.candidate().isEmpty()
            ? "empty_first_candidate"
            : !HAN.matcher(cleaned.candidate()).find()
                ? "no_han_first_candidate"
                : "unsafe_default_candidate";
    return excluded(row, reason, cleaned.matchedRules());
  }

  private static boolean hasQueryableSurface(LexiconImportRow row) {
    return LexiconSurfacePolicy.withinQueryWindow(row.lemma())
        || row.aliases().stream().anyMatch(LexiconSurfacePolicy::withinQueryWindow)
        || row.inflections().stream().anyMatch(LexiconSurfacePolicy::withinQueryWindow);
  }

  private static boolean nonAsciiLetter(int point) {
    return Character.isLetter(point)
        && !(point >= 'A' && point <= 'Z' || point >= 'a' && point <= 'z');
  }

  private static boolean withoutFrequencyProtection(LexiconImportRow row) {
    return !row.sourceOxfordBasic()
        && row.sourceBncRank() == null
        && row.sourceFrqRank() == null
        && row.priority().frequencyZipf() < 3;
  }

  private static boolean rareExpansion(LexiconImportRow row) {
    if (!withoutFrequencyProtection(row)
        || !SHORT_LEMMA.matcher(row.lemma()).matches()
        || !EXPANSION.matcher(row.sourceGloss()).find()) return false;
    long ascii =
        row.sourceGloss()
            .chars()
            .filter(point -> point >= 'A' && point <= 'Z' || point >= 'a' && point <= 'z')
            .count();
    long han =
        row.sourceGloss()
            .codePoints()
            .filter(
                point -> point >= 0x3400 && point <= 0x4dbf || point >= 0x4e00 && point <= 0x9fff)
            .count();
    return ascii >= 24 && han > 0 && (double) ascii / (ascii + han) >= 0.7;
  }

  private static PreparedHint ready(
      LexiconImportRow row, String gloss, String rule, List<String> matches) {
    var classification = ClassificationPolicy.classify(row, null);
    return new PreparedHint(gloss, null, rule, matches, classification);
  }

  private static PreparedHint excluded(LexiconImportRow row, String reason, List<String> matches) {
    var classification = ClassificationPolicy.classify(row, reason);
    return new PreparedHint(null, reason, reason, matches, classification);
  }

  private static boolean safeGloss(String gloss) {
    return StardictGlossPreparation.safeGloss(gloss);
  }
}
