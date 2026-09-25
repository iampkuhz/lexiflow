package io.lexiflow.lexicon.domain.model;

import java.text.Normalizer;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;

/**
 * 可复用词汇事实的一个不可变发布版本。
 *
 * @param entryId 词条稳定 UUID。
 * @param lexiconVersion 词条所属的单调词库版本。
 * @param languageTag 词条语言；首版固定为英语。
 * @param entryKind 单词或固定短语。
 * @param lemma 已规范化的 lemma 或短语表面。
 * @param senses 不可为空且具有唯一 identity 的义项集合。
 * @param aliases 指向同一词条的别名集合。
 * @param inflections 指向同一 lemma 的屈折形集合。
 * @param provenance 发布版本的来源和许可声明。
 * @param priority 非个人化的预热优先级。
 * @param hintEligibility 随版本发布的预处理资格，不由观看请求推断。
 */
public record LexiconEntry(
    UUID entryId,
    long lexiconVersion,
    String languageTag,
    LexiconEntryKind entryKind,
    String lemma,
    List<LexiconSense> senses,
    List<LexiconAlias> aliases,
    List<LexiconInflection> inflections,
    LexiconProvenance provenance,
    LexiconPriority priority,
    LexiconHintEligibility hintEligibility) {

  /** 校验版本化词条、表面唯一性和从属实体的不可变边界。 */
  public LexiconEntry {
    Objects.requireNonNull(entryId, "entryId");
    if (lexiconVersion < 1) {
      throw new IllegalArgumentException("lexiconVersion must be positive");
    }
    if (!"en".equals(languageTag)) {
      throw new IllegalArgumentException("languageTag must be en");
    }
    Objects.requireNonNull(entryKind, "entryKind");
    lemma = normalizeEnglishForm(lemma, "lemma");
    assertKindMatches(entryKind, lemma);
    senses = immutableUnique(senses, LexiconSense::senseId, "senses", true);
    aliases = immutableUnique(aliases, LexiconAlias::normalizedForm, "aliases", false);
    inflections =
        immutableUnique(inflections, LexiconInflection::normalizedForm, "inflections", false);
    rejectSurfaceReuse(lemma, aliases, inflections);
    Objects.requireNonNull(provenance, "provenance");
    Objects.requireNonNull(priority, "priority");
    Objects.requireNonNull(hintEligibility, "hintEligibility");
  }

  /** 构造明确由调用者准备的内置/受控词条；数据库读取须显式传入持久化资格。 */
  public LexiconEntry(
      UUID entryId,
      long lexiconVersion,
      String languageTag,
      LexiconEntryKind entryKind,
      String lemma,
      List<LexiconSense> senses,
      List<LexiconAlias> aliases,
      List<LexiconInflection> inflections,
      LexiconProvenance provenance,
      LexiconPriority priority) {
    this(
        entryId,
        lexiconVersion,
        languageTag,
        entryKind,
        lemma,
        senses,
        aliases,
        inflections,
        provenance,
        priority,
        LexiconHintEligibility.CANDIDATE);
  }

  /** 兼容没有频率证据的内置词条，导入词条必须显式提供优先级。 */
  public LexiconEntry(
      UUID entryId,
      long lexiconVersion,
      String languageTag,
      LexiconEntryKind entryKind,
      String lemma,
      List<LexiconSense> senses,
      List<LexiconAlias> aliases,
      List<LexiconInflection> inflections,
      LexiconProvenance provenance) {
    this(
        entryId,
        lexiconVersion,
        languageTag,
        entryKind,
        lemma,
        senses,
        aliases,
        inflections,
        provenance,
        LexiconPriority.unranked());
  }

  /**
   * 返回同一语言和词条类别内唯一的规范化查询键。
   *
   * @return 由语言、词条类别和 lemma 组成的规范键。
   */
  public String normalizedKey() {
    return languageTag + ":" + entryKind.name().toLowerCase(Locale.ROOT) + ":" + lemma;
  }

  /**
   * 返回可供确定性候选匹配的规范化表面。
   *
   * @return 非空白且已规范化的 lemma。
   */
  public String term() {
    return lemma;
  }

  /** 将英文表面规范为 NFC、小写和单一 ASCII 空格。 */
  static String normalizeEnglishForm(String value, String field) {
    Objects.requireNonNull(value, field);
    var normalized =
        Normalizer.normalize(value, Normalizer.Form.NFC).trim().replaceAll("\\s+", " ");
    if (normalized.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return normalized.toLowerCase(Locale.ROOT);
  }

  private static void assertKindMatches(LexiconEntryKind kind, String lemma) {
    var tokens = lemma.split(" ").length;
    if ((kind == LexiconEntryKind.WORD && tokens != 1)
        || (kind == LexiconEntryKind.PHRASE && tokens < 2)) {
      throw new IllegalArgumentException("entryKind does not match lemma token count");
    }
  }

  private static <T, K> List<T> immutableUnique(
      List<T> values, java.util.function.Function<T, K> key, String field, boolean required) {
    Objects.requireNonNull(values, field);
    var copied = List.copyOf(values);
    if (required && copied.isEmpty()) {
      throw new IllegalArgumentException(field + " must not be empty");
    }
    var keys = new HashSet<K>();
    for (var value : copied) {
      if (!keys.add(key.apply(value))) {
        throw new IllegalArgumentException(field + " must be unique");
      }
    }
    return copied;
  }

  private static void rejectSurfaceReuse(
      String lemma, List<LexiconAlias> aliases, List<LexiconInflection> inflections) {
    var forms = new HashSet<String>();
    forms.add(lemma);
    for (var alias : aliases) {
      if (!forms.add(alias.normalizedForm())) {
        throw new IllegalArgumentException("alias reuses an existing surface");
      }
    }
    for (var inflection : inflections) {
      if (!forms.add(inflection.normalizedForm())) {
        throw new IllegalArgumentException("inflection reuses an existing surface");
      }
    }
  }
}
