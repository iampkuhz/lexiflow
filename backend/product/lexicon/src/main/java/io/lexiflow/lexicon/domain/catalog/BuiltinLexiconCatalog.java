package io.lexiflow.lexicon.domain.catalog;

import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.OptionalLong;

/** 仅用于自动测试的有限内置材料，不替代产品已发布词库。 */
public final class BuiltinLexiconCatalog implements LexiconCatalog {
  private static final List<LexiconHintCandidate> ENTRIES =
      List.of(
          entry("figure out", "弄明白；理解"),
          entry("reliable", "可靠的"),
          entry("context", "语境；上下文"),
          entry("caption", "字幕；说明文字"),
          entry("deliver", "交付；传达"));

  /**
   * 返回字幕中的测试候选，优先更长的词段。
   *
   * @param normalizedForms 含义：已规范化的精确词形键。取值范围：非 null；每键非空白、规范且至多三个 token。
   * @return 固定内置材料中的精确匹配候选与本次计数。
   */
  @Override
  public LexiconLookupResult lookupForms(List<String> normalizedForms) {
    var keys = validate(normalizedForms);
    if (keys.isEmpty())
      return new LexiconLookupResult(
          List.of(), OptionalLong.empty(), LexiconLookupResult.Counts.zero());
    var found = ENTRIES.stream().filter(entry -> keys.contains(entry.normalizedForm())).toList();
    int positive =
        (int)
            keys.stream()
                .filter(key -> found.stream().anyMatch(e -> e.normalizedForm().equals(key)))
                .count();
    int negative = keys.size() - positive;
    return new LexiconLookupResult(
        found,
        OptionalLong.of(1),
        new LexiconLookupResult.Counts(keys.size(), positive, negative, 0, 0, 0, 0));
  }

  private static List<String> validate(List<String> forms) {
    Objects.requireNonNull(forms, "normalizedForms");
    var keys = new LinkedHashSet<String>();
    for (var form : List.copyOf(forms)) {
      Objects.requireNonNull(form, "normalized form");
      var tokens = LexiconSurfacePolicy.queryTokens(form);
      if (form.isBlank()
          || tokens.isEmpty()
          || tokens.size() > LexiconSurfacePolicy.MAX_PHRASE_TOKENS
          || !String.join(" ", tokens).equals(form)) {
        throw new IllegalArgumentException("normalized form is invalid");
      }
      keys.add(form);
    }
    return List.copyOf(keys);
  }

  private static LexiconHintCandidate entry(String lemma, String gloss) {
    return new LexiconHintCandidate(
        LexiconIdentity.entryId(lemma),
        LexiconIdentity.senseId(1, lemma),
        1,
        "en",
        lemma,
        lemma,
        lemma.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        LexiconHintAction.HINT,
        gloss,
        100,
        false,
        0);
  }
}
