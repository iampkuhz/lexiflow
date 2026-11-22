package io.lexiflow.api.hints.model;

import java.util.*;

/** v3 增量提示响应；发布版本只在顶层出现。 */
public record CaptionHintResponse(List<String> processedKeys, Long lexiconVersion, List<Hint> hints) {
  public CaptionHintResponse {
    processedKeys = List.copyOf(Objects.requireNonNull(processedKeys));
    hints = List.copyOf(Objects.requireNonNull(hints));
    if (lexiconVersion == null ? !hints.isEmpty() : lexiconVersion < 1) throw new IllegalArgumentException("invalid lexicon version");
  }
  public CaptionHintResponse(List<String> processedKeys, List<Hint> hints) { this(processedKeys, hints.isEmpty() ? null : 1L, hints); }
  public record Hint(String startKey, int startOffset, String endKey, int endOffset,
      String chineseGloss, String lexiconEntryId, Long frequencyRank) {
    /** Source compatibility for tests and callers that supplied redundant per-hint identities. */
    public Hint(String startKey, int startOffset, String endKey, int endOffset, String gloss,
        String entryId, long ignoredVersion, String ignoredSenseId, String ignoredLemma) {
      this(startKey, startOffset, endKey, endOffset, gloss, entryId, null);
    }
  }
}
