package io.lexiflow.lexicon.domain.catalog;

import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;

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
   * @param caption 含义：测试提供的字幕文本。取值范围：非 null，可为空字符串。
   * @return 固定内置材料中的匹配候选。
   */
  @Override
  public List<LexiconHintCandidate> candidatesFor(String caption) {
    Objects.requireNonNull(caption, "caption");
    var normalized = caption.toLowerCase(Locale.ROOT);
    return ENTRIES.stream()
        .filter(entry -> normalized.contains(entry.normalizedForm()))
        .sorted(
            (left, right) ->
                Integer.compare(right.normalizedForm().length(), left.normalizedForm().length()))
        .toList();
  }

  private static LexiconHintCandidate entry(String lemma, String gloss) {
    return new LexiconHintCandidate(
        stableId("entry:en:" + lemma),
        stableId("sense:" + lemma),
        1,
        "en",
        lemma,
        lemma,
        lemma.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        LexiconHintAction.HINT,
        gloss,
        100,
        0,
        0);
  }

  private static UUID stableId(String value) {
    return UUID.nameUUIDFromBytes(value.getBytes(StandardCharsets.UTF_8));
  }
}
