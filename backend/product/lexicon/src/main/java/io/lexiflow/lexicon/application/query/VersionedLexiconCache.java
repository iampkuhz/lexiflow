package io.lexiflow.lexicon.application.query;

import io.lexiflow.lexicon.application.port.LexiconCacheObserver;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
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
  private WarmupStatus status = new WarmupStatus(-1, 0, 0, 0, false);

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

  Refresh refresh(LexiconReadRepository repository, LexiconCacheObserver observer) {
    var version = repository.publishedVersion();
    if (version == cachedVersion) return new Refresh(version, 0);
    long started = System.nanoTime();
    long previous = cachedVersion;
    int oldPositive = 0, oldNegative = 0;
    var oldKeys = new java.util.LinkedHashMap<String, List<LexiconHintCandidate>>(pinned);
    dynamic.forEach(oldKeys::putIfAbsent);
    for (var values : oldKeys.values()) {
      if (values.stream().anyMatch(c -> c.finalAction() == LexiconHintAction.HINT)) oldPositive++;
      else oldNegative++;
    }
    pinned.clear();
    dynamic.clear();
    cachedVersion = version;
    if (previous >= 0) {
      try {
        observer.versionChanged(
            previous, version, oldPositive, oldNegative, Math.max(0, System.nanoTime() - started));
      } catch (RuntimeException ignored) {
      }
    }
    int reads = 0;
    boolean degraded = false;
    if (version > 0) {
      if (positivePrewarmLimit > 0) {
        reads++;
        try {
          prewarm(repository, version, LexiconHintAction.HINT, positivePrewarmLimit);
        } catch (RuntimeException failure) {
          degraded = true;
        }
      }
      if (negativePrewarmLimit > 0) {
        reads++;
        try {
          prewarm(repository, version, LexiconHintAction.BLOCK, negativePrewarmLimit);
        } catch (RuntimeException failure) {
          degraded = true;
        }
      }
    }
    int positive = 0;
    int negative = 0;
    for (var candidates : pinned.values()) {
      if (candidates.stream().anyMatch(c -> c.finalAction() == LexiconHintAction.HINT)) positive++;
      else negative++;
    }
    status = new WarmupStatus(version, positive, negative, reads, degraded);
    return new Refresh(version, reads);
  }

  WarmupStatus status() {
    return status;
  }

  private void prewarm(
      LexiconReadRepository repository, long version, LexiconHintAction action, int limit) {
    var rows = List.copyOf(repository.findPrewarmForms(version, action, limit));
    var grouped = groupByForm(rows);
    if (grouped.size() > limit) throw new IllegalStateException("prewarm budget exceeded");
    for (var candidate : rows) {
      if (candidate.lexiconVersion() != version
          || candidate.normalizedForm() == null
          || candidate.normalizedForm().isBlank())
        throw new IllegalStateException("invalid prewarm candidate");
    }
    for (var group : grouped.entrySet()) {
      boolean selected = group.getValue().stream().anyMatch(c -> c.finalAction() == action);
      if (!selected) throw new IllegalStateException("invalid prewarm action");
    }
    for (var group : grouped.entrySet()) pin(group.getKey(), group.getValue());
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
    var unique = new LinkedHashMap<Long, LexiconHintCandidate>();
    for (var candidate : left) unique.put(candidate.entryId(), candidate);
    for (var candidate : right) unique.put(candidate.entryId(), candidate);
    return List.copyOf(unique.values());
  }

  /**
   * 本次版本检查及预热读取量。
   *
   * @param version 含义：刷新后绑定的发布版本。取值范围：非负版本号。
   * @param prewarmReads 含义：本次刷新执行的预热读取批次数。取值范围：非负整数。
   */
  record Refresh(long version, int prewarmReads) {}

  /**
   * 缓存内部的版本预热状态。
   *
   * @param version 绑定版本。
   * @param positiveKeys 实际固定正向键数。
   * @param negativeKeys 实际固定负向键数。
   * @param attempts 实际预热读取次数。
   * @param degraded 是否有失败批次。
   */
  record WarmupStatus(
      long version, int positiveKeys, int negativeKeys, int attempts, boolean degraded) {}
}
