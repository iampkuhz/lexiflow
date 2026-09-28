package io.lexiflow.lexicon.application.query;

import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 管理与发布版本绑定的固定预热项和有界动态缓存。 */
final class VersionedLexiconCache {
  private final int capacity;
  private final int pinnedCapacity;
  private final int positivePrewarmLimit;
  private final int negativePrewarmLimit;
  private final Map<String, List<LexiconHintCandidate>> pinned = new LinkedHashMap<>();
  private final Map<String, List<LexiconHintCandidate>> dynamic;
  private long cachedVersion = -1;

  VersionedLexiconCache(int capacity, int positivePrewarmLimit, int negativePrewarmLimit) {
    if (capacity < 1
        || positivePrewarmLimit < 0
        || negativePrewarmLimit < 0
        || (long) positivePrewarmLimit + negativePrewarmLimit > capacity) {
      throw new IllegalArgumentException("cache capacity or prewarm limit is invalid");
    }
    this.capacity = capacity;
    this.positivePrewarmLimit = positivePrewarmLimit;
    this.negativePrewarmLimit = negativePrewarmLimit;
    this.pinnedCapacity = positivePrewarmLimit + negativePrewarmLimit;
    this.dynamic = new LinkedHashMap<>(capacity, 0.75F, true);
  }

  Refresh refresh(LexiconRepository repository) {
    var version = repository.publishedVersion();
    if (version == cachedVersion) return new Refresh(version, 0);
    pinned.clear();
    dynamic.clear();
    cachedVersion = version;
    var reads = 0;
    if (version > 0) {
      reads += prewarm(repository, version, LexiconHintAction.HINT, positivePrewarmLimit);
      reads += prewarm(repository, version, LexiconHintAction.BLOCK, negativePrewarmLimit);
    }
    return new Refresh(version, reads);
  }

  private int prewarm(
      LexiconRepository repository, long version, LexiconHintAction action, int limit) {
    if (limit == 0) return 0;
    var grouped = groupByForm(repository.findPrewarmForms(version, action, limit));
    for (var group : grouped.entrySet()) pin(group.getKey(), group.getValue());
    return 1;
  }

  static Map<String, List<LexiconHintCandidate>> groupByForm(
      List<LexiconHintCandidate> candidates) {
    var groups = new LinkedHashMap<String, List<LexiconHintCandidate>>();
    for (var candidate : candidates) {
      groups
          .computeIfAbsent(candidate.normalizedForm(), ignored -> new ArrayList<>())
          .add(candidate);
    }
    groups.replaceAll((ignored, values) -> List.copyOf(values));
    return groups;
  }

  boolean contains(String form) {
    return pinned.containsKey(form) || dynamic.containsKey(form);
  }

  List<LexiconHintCandidate> get(String form) {
    return pinned.containsKey(form) ? pinned.get(form) : dynamic.get(form);
  }

  void pin(String form, List<LexiconHintCandidate> candidates) {
    if (pinned.size() >= pinnedCapacity && !pinned.containsKey(form)) return;
    pinned.merge(form, candidates, VersionedLexiconCache::merge);
  }

  void putDynamic(String form, List<LexiconHintCandidate> candidates) {
    dynamic.put(form, candidates);
    while (dynamic.size() + pinned.size() > capacity) {
      dynamic.remove(dynamic.keySet().iterator().next());
    }
  }

  private static List<LexiconHintCandidate> merge(
      List<LexiconHintCandidate> left, List<LexiconHintCandidate> right) {
    var unique = new LinkedHashMap<String, LexiconHintCandidate>();
    for (var candidate : left) unique.put(candidate.entryId().toString(), candidate);
    for (var candidate : right) unique.put(candidate.entryId().toString(), candidate);
    return List.copyOf(unique.values());
  }

  /**
   * 本次版本检查及预热读取量。
   *
   * @param version 含义：刷新后绑定的发布版本。取值范围：非负版本号。
   * @param prewarmReads 含义：本次刷新执行的预热读取批次数。取值范围：非负整数。
   */
  record Refresh(long version, int prewarmReads) {}
}
