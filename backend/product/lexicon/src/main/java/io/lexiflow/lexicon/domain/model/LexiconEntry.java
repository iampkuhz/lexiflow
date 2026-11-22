package io.lexiflow.lexicon.domain.model;

import java.text.Normalizer;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Objects;

/**
 * 可复用词汇事实的一个不可变发布版本。
 *
 * @param entryId 含义：词条稳定身份。取值范围：正 long，由语言和规范 lemma 确定。
 * @param lexiconVersion 含义：词条所属词库版本。取值范围：正 long，随发布单调递增。
 * @param languageTag 含义：词条语言标记。取值范围：当前仅为 en。
 * @param entryKind 含义：词条类别。取值范围：单词或固定短语。
 * @param lemma 含义：规范主词形或短语表面。取值范围：非空且符合词条类别。
 * @param senses 含义：词条的义项集合。取值范围：非空且义项身份唯一。
 * @param aliases 含义：指向同一词条的别名集合。取值范围：表面唯一，可为空。
 * @param inflections 含义：指向同一主词形的屈折形集合。取值范围：表面唯一，可为空。
 * @param provenance 含义：发布版本的来源与许可声明。取值范围：非空。
 * @param priority 含义：发布查询优先级输入。取值范围：非空，优先级语义由领域类型限定。
 * @param frequencyRank 含义：已发布原始 BNC 排名。取值范围：可空；存在时为正数，不使用 FRQ 回退。
 */
public record LexiconEntry(
    long entryId,
    long lexiconVersion,
    String languageTag,
    LexiconEntryKind entryKind,
    String lemma,
    List<LexiconSense> senses,
    List<LexiconAlias> aliases,
    List<LexiconInflection> inflections,
    LexiconProvenance provenance,
    LexiconPriority priority,
    Long frequencyRank) {

  /** 校验版本化词条、表面唯一性和从属实体的不可变边界。 */
  public LexiconEntry {
    if (entryId < 1) throw new IllegalArgumentException("entryId must be positive");
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
    if (frequencyRank != null && frequencyRank < 1) {
      throw new IllegalArgumentException("frequencyRank must be positive when present");
    }
  }

  /** 创建不含来源词频排名的词条。 */
  public LexiconEntry(
      long entryId,
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
        null);
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
