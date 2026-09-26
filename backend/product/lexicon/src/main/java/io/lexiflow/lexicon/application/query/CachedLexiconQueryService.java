package io.lexiflow.lexicon.application.query;

import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/** 只查已发布的准确词形；固定正负缓存与有界动态缓存都可重建。 */
public final class CachedLexiconQueryService implements LexiconCatalog {
  private static final int MAX_PHRASE_TOKENS = 3;
  private final LexiconRepository repository;
  private final int cacheCapacity;
  private final int positivePrewarmLimit;
  private final int negativePrewarmLimit;
  private final Map<String, List<LexiconHintCandidate>> pinned = new LinkedHashMap<>();
  private final Map<String, List<LexiconHintCandidate>> dynamic;
  private long cachedVersion = -1;

  /** 分别限定提示和阻断预热词形，避免调用者控制词典筛选策略。 */
  public CachedLexiconQueryService(
      LexiconRepository repository,
      int cacheCapacity,
      int positivePrewarmLimit,
      int negativePrewarmLimit) {
    this.repository = Objects.requireNonNull(repository, "repository");
    if (cacheCapacity < 1
        || positivePrewarmLimit < 0
        || negativePrewarmLimit < 0
        || positivePrewarmLimit + negativePrewarmLimit > cacheCapacity) {
      throw new IllegalArgumentException("cache capacity or prewarm limit is invalid");
    }
    this.cacheCapacity = cacheCapacity;
    this.positivePrewarmLimit = positivePrewarmLimit;
    this.negativePrewarmLimit = negativePrewarmLimit;
    this.dynamic = new LinkedHashMap<>(cacheCapacity, 0.75F, true);
    refreshVersion();
  }

  /**
   * 对字幕仅枚举连续的一至三个词形，缺失词形在同一批查询中读取。
   *
   * @param caption 含义：待匹配的原始字幕片段。取值范围：非 null，可为空字符串。
   * @return 按词形长度排序且保留同形歧义的已发布候选。
   */
  @Override
  public synchronized List<LexiconHintCandidate> candidatesFor(String caption) {
    Objects.requireNonNull(caption, "caption");
    var version = refreshVersion();
    if (version == 0) return List.of();
    var keys = candidateForms(caption);
    var missing = new ArrayList<String>();
    for (var form : keys) {
      if (!pinned.containsKey(form) && !dynamic.containsKey(form)) missing.add(form);
    }
    if (!missing.isEmpty()) {
      var loaded = groupByForm(repository.findByForms(version, missing));
      for (var form : missing) {
        dynamic.put(form, loaded.getOrDefault(form, List.of()));
        trimDynamic();
      }
    }
    var result = new LinkedHashMap<String, LexiconHintCandidate>();
    for (var form : keys) {
      var candidates = pinned.get(form);
      if (candidates == null) candidates = dynamic.getOrDefault(form, List.of());
      for (var candidate : candidates) {
        result.put(candidate.entryId() + ":" + candidate.normalizedForm(), candidate);
      }
    }
    return result.values().stream()
        .sorted(
            (left, right) -> {
              var length =
                  Integer.compare(right.normalizedForm().length(), left.normalizedForm().length());
              return length != 0 ? length : left.normalizedForm().compareTo(right.normalizedForm());
            })
        .toList();
  }

  private long refreshVersion() {
    var version = repository.publishedVersion();
    if (version != cachedVersion) {
      pinned.clear();
      dynamic.clear();
      cachedVersion = version;
      if (version > 0) {
        prewarm(version, LexiconHintAction.HINT, positivePrewarmLimit);
        prewarm(version, LexiconHintAction.BLOCK, negativePrewarmLimit);
      }
    }
    return version;
  }

  private void prewarm(long version, LexiconHintAction action, int limit) {
    if (limit == 0) return;
    var grouped = groupByForm(repository.findPrewarmForms(version, action, limit));
    for (var group : grouped.entrySet()) {
      if (pinned.size() >= positivePrewarmLimit + negativePrewarmLimit
          && !pinned.containsKey(group.getKey())) break;
      pinned.merge(group.getKey(), group.getValue(), CachedLexiconQueryService::merge);
    }
  }

  private static List<LexiconHintCandidate> merge(
      List<LexiconHintCandidate> left, List<LexiconHintCandidate> right) {
    var unique = new LinkedHashMap<String, LexiconHintCandidate>();
    for (var candidate : left) unique.put(candidate.entryId().toString(), candidate);
    for (var candidate : right) unique.put(candidate.entryId().toString(), candidate);
    return List.copyOf(unique.values());
  }

  private static Map<String, List<LexiconHintCandidate>> groupByForm(
      Collection<LexiconHintCandidate> candidates) {
    var groups = new LinkedHashMap<String, List<LexiconHintCandidate>>();
    for (var candidate : candidates) {
      groups
          .computeIfAbsent(candidate.normalizedForm(), ignored -> new ArrayList<>())
          .add(candidate);
    }
    groups.replaceAll((ignored, values) -> List.copyOf(values));
    return groups;
  }

  private void trimDynamic() {
    while (dynamic.size() + pinned.size() > cacheCapacity) {
      dynamic.remove(dynamic.keySet().iterator().next());
    }
  }

  private static List<String> candidateForms(String caption) {
    var normalized =
        caption.toLowerCase(Locale.ROOT).replaceAll("[^\\p{IsAlphabetic}']+", " ").trim();
    if (normalized.isEmpty()) return List.of();
    var tokens = normalized.split(" +");
    var forms = new LinkedHashSet<String>();
    for (var start = 0; start < tokens.length; start++) {
      var phrase = new StringBuilder();
      for (var length = 1;
          length <= MAX_PHRASE_TOKENS && start + length <= tokens.length;
          length++) {
        if (length > 1) phrase.append(' ');
        phrase.append(tokens[start + length - 1]);
        forms.add(phrase.toString());
      }
    }
    return List.copyOf(forms);
  }
}
