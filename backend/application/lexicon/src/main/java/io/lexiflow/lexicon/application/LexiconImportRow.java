package io.lexiflow.lexicon.application;

import io.lexiflow.lexicon.domain.LexiconPriority;
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
    boolean prewarmEligible) {
  /** 构造不可变的导入记录。 */
  public LexiconImportRow {
    lemma = required(lemma, "lemma");
    chineseGloss = required(chineseGloss, "chineseGloss");
    definition = Objects.requireNonNullElse(definition, "").trim();
    aliases = List.copyOf(Objects.requireNonNull(aliases, "aliases"));
    inflections = List.copyOf(Objects.requireNonNull(inflections, "inflections"));
    priority = Objects.requireNonNull(priority, "priority");
    dictionary = Objects.requireNonNull(dictionary, "dictionary");
    frequency = Objects.requireNonNull(frequency, "frequency");
    complexLists = List.copyOf(Objects.requireNonNull(complexLists, "complexLists"));
  }

  private static String required(String value, String field) {
    var required = Objects.requireNonNull(value, field).trim();
    if (required.isEmpty()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return required;
  }
}
