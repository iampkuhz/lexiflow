package io.lexiflow.lexicon.application.query;

import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.OptionalLong;

/** 只查已发布的准确词形；固定正负缓存与有界动态缓存都可重建。 */
public final class CachedLexiconQueryService implements LexiconCatalog {
  private final LexiconReadRepository repository;
  private final VersionedLexiconCache cache;

  /** 分别限定提示和阻断预热词形，避免调用者控制词典筛选策略。 */
  public CachedLexiconQueryService(
      LexiconReadRepository repository,
      int cacheCapacity,
      int positivePrewarmLimit,
      int negativePrewarmLimit) {
    this.repository = Objects.requireNonNull(repository, "repository");
    this.cache =
        new VersionedLexiconCache(cacheCapacity, positivePrewarmLimit, negativePrewarmLimit);
    cache.refresh(repository);
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
    var refresh = cache.refresh(repository);
    var version = refresh.version();
    metrics[4] = refresh.prewarmReads();
    var missing = new ArrayList<String>();
    var requestResults = new LinkedHashMap<String, List<LexiconHintCandidate>>();
    for (var form : keys) {
      var cached = cache.get(form);
      if (!cache.contains(form)) {
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
      var loaded = VersionedLexiconCache.groupByForm(repository.findByForms(version, missing));
      for (var form : missing) {
        var found = loaded.getOrDefault(form, List.of());
        requestResults.put(form, found);
        cache.putDynamic(form, found);
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
}
