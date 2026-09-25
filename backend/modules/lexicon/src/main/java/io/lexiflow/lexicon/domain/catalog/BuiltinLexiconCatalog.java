package io.lexiflow.lexicon.domain.catalog;

import io.lexiflow.lexicon.domain.model.LexiconEntry;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconProvenance;
import io.lexiflow.lexicon.domain.model.LexiconSense;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;

/** 首版内置的有限词汇材料，只提供确定性提示而不代表完整词典。 */
public final class BuiltinLexiconCatalog implements LexiconCatalog {
  private static final LexiconProvenance BUILTIN_PROVENANCE =
      new LexiconProvenance(
          "lexiflow-builtins", "LicenseRef-LexiFlow-Internal", "builtin-v1", Instant.EPOCH);
  private static final List<LexiconEntry> ENTRIES =
      List.of(
          entry("figure out", "弄明白；理解"),
          entry("reliable", "可靠的"),
          entry("context", "语境；上下文"),
          entry("caption", "字幕；说明文字"),
          entry("deliver", "交付；传达"));

  /**
   * 返回出现在字幕中的内置候选，优先返回更长词段。
   *
   * @param caption 含义：待匹配的英文字幕。取值范围：非空，可为空白字符串。
   * @return 按词段长度降序排列的内置候选。
   */
  @Override
  public List<LexiconEntry> candidatesFor(String caption) {
    Objects.requireNonNull(caption, "caption");
    var normalized = caption.toLowerCase(Locale.ROOT);
    return ENTRIES.stream()
        .filter(entry -> normalized.contains(entry.term()))
        .sorted((left, right) -> Integer.compare(right.term().length(), left.term().length()))
        .toList();
  }

  private static LexiconEntry entry(String lemma, String gloss) {
    var entryId = stableId("entry:" + lemma);
    return new LexiconEntry(
        entryId,
        1,
        "en",
        lemma.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        lemma,
        List.of(new LexiconSense(stableId("sense:" + lemma), gloss, lemma, "lexiflow-builtins")),
        List.of(),
        List.of(),
        BUILTIN_PROVENANCE);
  }

  private static UUID stableId(String value) {
    return UUID.nameUUIDFromBytes(value.getBytes(StandardCharsets.UTF_8));
  }
}
