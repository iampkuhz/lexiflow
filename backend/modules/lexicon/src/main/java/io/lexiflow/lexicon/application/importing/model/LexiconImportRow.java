package io.lexiflow.lexicon.application.importing.model;

import io.lexiflow.lexicon.application.importing.validation.BasicVocabulary;
import io.lexiflow.lexicon.application.importing.validation.GlossPreparation;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.util.List;
import java.util.Objects;

/**
 * 已解析且受来源合同约束的一条词库导入记录。
 *
 * @param lemma 含义：词条原形。取值范围：由方法调用前置条件限定。
 * @param chineseGloss 含义：中文释义。取值范围：由方法调用前置条件限定。
 * @param definition 含义：来源定义。取值范围：由方法调用前置条件限定。
 * @param aliases 含义：同义或别名表面。取值范围：由方法调用前置条件限定。
 * @param inflections 含义：屈折表面。取值范围：由方法调用前置条件限定。
 * @param priority 含义：排序与预热优先级。取值范围：由方法调用前置条件限定。
 * @param dictionary 含义：词典字段来源。取值范围：由方法调用前置条件限定。
 * @param frequency 含义：词频字段来源。取值范围：由方法调用前置条件限定。
 * @param complexLists 含义：复杂词表字段来源。取值范围：由方法调用前置条件限定。
 * @param prewarmEligible 含义：是否可进入预热候选。取值范围：由方法调用前置条件限定。
 * @param basicVocabulary 导入时已确定的基础词排除标记。
 * @param hintPolicyReference 基础词选择和清洗规则的来源引用。
 */
public record LexiconImportRow(
    String lemma,
    String chineseGloss,
    String definition,
    List<String> aliases,
    List<String> inflections,
    LexiconPriority priority,
    SourceReference dictionary,
    SourceReference frequency,
    List<SourceReference> complexLists,
    boolean prewarmEligible,
    boolean basicVocabulary,
    String hintPolicyReference) {
  /** 构造不可变的导入记录。 */
  public LexiconImportRow {
    lemma = required(lemma, "lemma");
    chineseGloss = GlossPreparation.normalize(required(chineseGloss, "chineseGloss"));
    definition = Objects.requireNonNullElse(definition, "").trim();
    aliases = List.copyOf(Objects.requireNonNull(aliases, "aliases"));
    inflections = List.copyOf(Objects.requireNonNull(inflections, "inflections"));
    priority = Objects.requireNonNull(priority, "priority");
    dictionary = Objects.requireNonNull(dictionary, "dictionary");
    frequency = Objects.requireNonNull(frequency, "frequency");
    complexLists = List.copyOf(Objects.requireNonNull(complexLists, "complexLists"));
    basicVocabulary =
        basicVocabulary
            || BasicVocabulary.contains(lemma)
            || aliases.stream().anyMatch(BasicVocabulary::contains)
            || inflections.stream().anyMatch(BasicVocabulary::contains);
    hintPolicyReference = required(hintPolicyReference, "hintPolicyReference");
    if (basicVocabulary && lemma.contains(" "))
      throw new IllegalArgumentException("basic exclusion must name a word, not a phrase");
    prewarmEligible = prewarmEligible && !basicVocabulary;
  }

  /** 规范来源使用固定功能词规则；扩展基础词须由上游提供明确标记和依据。 */
  public LexiconImportRow(
      String lemma,
      String chineseGloss,
      String definition,
      List<String> aliases,
      List<String> inflections,
      LexiconPriority priority,
      SourceReference dictionary,
      SourceReference frequency,
      List<SourceReference> complexLists,
      boolean prewarmEligible) {
    this(
        lemma,
        chineseGloss,
        definition,
        aliases,
        inflections,
        priority,
        dictionary,
        frequency,
        complexLists,
        prewarmEligible,
        false,
        "fixed-function-words-and-identical-gloss-v1");
  }

  private static String required(String value, String field) {
    var required = Objects.requireNonNull(value, field).trim();
    if (required.isEmpty()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return required;
  }
}
