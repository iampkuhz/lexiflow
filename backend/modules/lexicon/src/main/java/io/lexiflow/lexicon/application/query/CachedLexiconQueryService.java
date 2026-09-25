package io.lexiflow.lexicon.application.query;

import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconEntry;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/** 以已发布版本查询词库，并维护可重建的有界 L1 候选缓存。 */
public final class CachedLexiconQueryService implements LexiconCatalog {
  private static final int MAX_PHRASE_TOKENS = 5;
  private final LexiconRepository repository;
  private final int cacheCapacity;
  private final Map<String, List<LexiconEntry>> cache;
  private long cachedVersion = -1;

  /** 构造查询服务并按当前已发布版本预热有限候选。 */
  public CachedLexiconQueryService(
      LexiconRepository repository, int cacheCapacity, int prewarmLimit) {
    this.repository = Objects.requireNonNull(repository, "repository");
    if (cacheCapacity < 1 || prewarmLimit < 0 || prewarmLimit > cacheCapacity) {
      throw new IllegalArgumentException("cache capacity or prewarm limit is invalid");
    }
    this.cacheCapacity = cacheCapacity;
    this.cache = new LinkedHashMap<>(cacheCapacity, 0.75F, true);
    refreshAndPrewarm(prewarmLimit);
  }

  /**
   * 查询字幕中可匹配的词条，未命中不会创建任何持久化事实。
   *
   * @param caption 含义：要匹配的字幕文本。取值范围：由方法调用前置条件限定。
   * @return 按最长词条优先排序的匹配候选
   */
  @Override
  public synchronized List<LexiconEntry> candidatesFor(String caption) {
    Objects.requireNonNull(caption, "caption");
    var version = refreshVersion();
    if (version == 0) {
      return List.of();
    }
    var result = new LinkedHashMap<UUID, LexiconEntry>();
    var missing = new ArrayList<String>();
    for (var key : candidateKeys(caption)) {
      var cached = cache.get(key);
      if (cached == null) {
        missing.add(key);
      } else {
        cached.forEach(entry -> result.put(entry.entryId(), entry));
      }
    }
    if (!missing.isEmpty()) {
      var loaded = entriesByForm(version, missing);
      for (var key : missing) {
        var entries = loaded.getOrDefault(surfaceFromKey(key), List.of());
        cache(key, entries);
        entries.forEach(entry -> result.put(entry.entryId(), entry));
      }
    }
    return result.values().stream()
        .sorted((left, right) -> Integer.compare(right.term().length(), left.term().length()))
        .toList();
  }

  private void refreshAndPrewarm(int prewarmLimit) {
    synchronized (this) {
      var version = refreshVersion();
      if (version == 0 || prewarmLimit == 0) {
        return;
      }
      repository
          .findPrewarmCandidates(version, prewarmLimit)
          .forEach(entry -> cache(entry.normalizedKey(), List.of(entry)));
    }
  }

  private long refreshVersion() {
    var version = repository.publishedVersion();
    if (version != cachedVersion) {
      cache.clear();
      cachedVersion = version;
    }
    return version;
  }

  private Map<String, List<LexiconEntry>> entriesByForm(long version, Collection<String> keys) {
    var results = new LinkedHashMap<String, List<LexiconEntry>>();
    var entries =
        repository.findByForms(
            version, keys.stream().map(CachedLexiconQueryService::surfaceFromKey).toList());
    for (var key : keys) {
      var surface = surfaceFromKey(key);
      results.put(surface, entries.stream().filter(entry -> hasSurface(entry, surface)).toList());
    }
    return results;
  }

  private static boolean hasSurface(LexiconEntry entry, String surface) {
    return entry.lemma().equals(surface)
        || entry.aliases().stream().anyMatch(alias -> alias.normalizedForm().equals(surface))
        || entry.inflections().stream()
            .anyMatch(inflection -> inflection.normalizedForm().equals(surface));
  }

  private static List<String> candidateKeys(String caption) {
    var normalized =
        caption.toLowerCase(Locale.ROOT).replaceAll("[^\\p{IsAlphabetic}']+", " ").trim();
    if (normalized.isEmpty()) {
      return List.of();
    }
    var tokens = normalized.split(" +");
    var keys = new java.util.LinkedHashSet<String>();
    for (var start = 0; start < tokens.length; start++) {
      var phrase = new StringBuilder();
      for (var length = 1;
          length <= MAX_PHRASE_TOKENS && start + length <= tokens.length;
          length++) {
        if (length > 1) {
          phrase.append(' ');
        }
        phrase.append(tokens[start + length - 1]);
        keys.add("en:" + (length == 1 ? "word" : "phrase") + ":" + phrase);
      }
    }
    return List.copyOf(keys);
  }

  private static String surfaceFromKey(String normalizedKey) {
    return normalizedKey.substring(normalizedKey.indexOf(':', 3) + 1);
  }

  private void cache(String key, List<LexiconEntry> entries) {
    cache.put(key, List.copyOf(entries));
    while (cache.size() > cacheCapacity) {
      cache.remove(cache.keySet().iterator().next());
    }
  }
}
