package io.lexiflow.lexicon.application.query;

import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.OptionalLong;

/** 只查已发布的准确词形；固定正负缓存与有界动态缓存都可重建。 */
public final class CachedLexiconQueryService implements LexiconCatalog {
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
   * 对已规范键精确查询，完整返回本次结果且不受有界缓存淘汰影响。
   *
   * @param normalizedForms 含义：Enrichment 生成的规范键。取值范围：非 null；键非空白、已规范且至多三词。
   * @return 完整候选、当前发布身份和本次请求局部计数。
   */
  @Override
  public synchronized LexiconLookupResult lookupForms(List<String> normalizedForms) {
    var keys = validate(normalizedForms);
    if (keys.isEmpty())
      return new LexiconLookupResult(
          List.of(), OptionalLong.empty(), LexiconLookupResult.Counts.zero());
    var metrics = new int[5]; // 依次记录正命中、负命中、缓存未命中、数据库批次和预热读取
    var version = refreshVersion(metrics);
    var missing = new ArrayList<String>();
    var requestResults = new LinkedHashMap<String, List<LexiconHintCandidate>>();
    for (var form : keys) {
      var cached = pinned.get(form);
      if (cached == null && !pinned.containsKey(form)) cached = dynamic.get(form);
      if (cached == null && !pinned.containsKey(form) && !dynamic.containsKey(form)) {
        missing.add(form);
      } else {
        requestResults.put(form, cached == null ? List.of() : cached);
        boolean positive =
            requestResults.get(form).stream()
                .anyMatch(candidate -> candidate.finalAction() == LexiconHintAction.HINT);
        if (positive) metrics[0]++;
        else metrics[1]++;
      }
    }
    metrics[2] = missing.size();
    if (!missing.isEmpty() && version > 0) {
      metrics[3] = 1;
      var loaded = groupByForm(repository.findByForms(version, missing));
      for (var form : missing) {
        var found = loaded.getOrDefault(form, List.of());
        requestResults.put(form, found);
        dynamic.put(form, found);
        trimDynamic();
      }
    } else {
      for (var form : missing) requestResults.put(form, List.of());
    }
    var result = new LinkedHashMap<String, LexiconHintCandidate>();
    for (var form : keys) {
      var candidates = requestResults.get(form);
      for (var candidate : candidates) {
        result.put(candidate.entryId() + ":" + candidate.normalizedForm(), candidate);
      }
    }
    var candidates =
        result.values().stream()
            .sorted(
                (left, right) -> {
                  var length =
                      Integer.compare(
                          right.normalizedForm().length(), left.normalizedForm().length());
                  return length != 0
                      ? length
                      : left.normalizedForm().compareTo(right.normalizedForm());
                })
            .toList();
    return new LexiconLookupResult(
        candidates,
        OptionalLong.of(version),
        new LexiconLookupResult.Counts(
            keys.size(), metrics[0], metrics[1], metrics[2], metrics[3], 1, metrics[4]));
  }

  private long refreshVersion(int[] metrics) {
    var version = repository.publishedVersion();
    if (version != cachedVersion) {
      pinned.clear();
      dynamic.clear();
      cachedVersion = version;
      if (version > 0) {
        prewarm(version, LexiconHintAction.HINT, positivePrewarmLimit, metrics);
        prewarm(version, LexiconHintAction.BLOCK, negativePrewarmLimit, metrics);
      }
    }
    return version;
  }

  private void prewarm(long version, LexiconHintAction action, int limit, int[] metrics) {
    if (limit == 0) return;
    metrics[4]++;
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

  /** 构造期间启动预热不计入后续请求的局部计数。 */
  private long refreshVersion() {
    return refreshVersion(new int[5]);
  }
}
