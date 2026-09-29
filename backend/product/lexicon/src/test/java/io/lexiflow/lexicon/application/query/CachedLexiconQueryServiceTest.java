package io.lexiflow.lexicon.application.query;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.port.LexiconCacheObserver;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 验证缓存查询服务只使用 Repository 和领域模型。 */
class CachedLexiconQueryServiceTest {
  @Test
  void reportsActualDynamicInvalidationOnlyAfterAnAlreadyBoundVersionChanges() {
    var repository = new CountingRepository();
    repository.entries = List.of(entry(1, "reliable", "可靠"));
    var changes = new java.util.ArrayList<List<Long>>();
    LexiconCacheObserver observer =
        (oldVersion, newVersion, positive, negative, nanos) ->
            changes.add(List.of(oldVersion, newVersion, (long) positive, (long) negative, nanos));
    var service = new CachedLexiconQueryService(repository, 8, 0, 0, observer);
    assertEquals(0, changes.size());
    service.lookupForms(List.of("reliable"));
    repository.version = 2;
    service.lookupForms(List.of("reliable"));
    assertEquals(1, changes.size());
    assertEquals(List.of(1L, 2L, 1L, 0L), changes.getFirst().subList(0, 4));
    assertEquals(0, service.refresh().version() == 2 ? changes.size() - 1 : -1);
  }

  @Test
  void reportsZeroVersionChangeButNotInitialBindingSameVersionOrVersionReadFailure() {
    var repository = new CountingRepository();
    repository.version = 0;
    var changes = new java.util.ArrayList<List<Long>>();
    var service =
        new CachedLexiconQueryService(
            repository,
            8,
            0,
            0,
            (oldVersion, newVersion, positive, negative, duration) ->
                changes.add(List.of(oldVersion, newVersion, (long) positive, (long) negative)));
    assertEquals(0, changes.size());
    repository.version = 1;
    service.lookupForms(List.of("missing"));
    assertEquals(List.of(List.of(0L, 1L, 0L, 0L)), changes);
    service.refresh();
    assertEquals(1, changes.size());
    repository.failVersionRead = true;
    assertThrows(IllegalStateException.class, service::refresh);
    assertEquals(1, changes.size());
  }

  @Test
  void countsMixedPinnedAndDynamicKeysBeforeClearAndIsolatesObserverAndPrewarmFailures() {
    var repository = new CountingRepository();
    var hint = entry(1, "bank", "银行");
    var block =
        new LexiconHintCandidate(
            UUID.nameUUIDFromBytes(
                "blocked-bank".getBytes(java.nio.charset.StandardCharsets.UTF_8)),
            null,
            1,
            "en",
            "bank",
            "bank",
            LexiconEntryKind.WORD,
            LexiconHintAction.BLOCK,
            null,
            0,
            0,
            0);
    repository.prewarm = List.of(hint, block);
    var changes = new java.util.ArrayList<List<Long>>();
    var prewarmCallsAtInvalidation = new int[1];
    var service =
        new CachedLexiconQueryService(
            repository,
            4,
            1,
            1,
            (oldVersion, newVersion, positive, negative, duration) -> {
              changes.add(List.of((long) positive, (long) negative));
              prewarmCallsAtInvalidation[0] = repository.prewarmCalls;
              throw new IllegalStateException("synthetic observer failure");
            });
    service.lookupForms(List.of("unknown"));
    repository.version = 2;
    service.lookupForms(List.of("bank"));
    assertEquals(List.of(List.of(1L, 1L)), changes);
    assertEquals(2, prewarmCallsAtInvalidation[0]);
    assertEquals(4, repository.prewarmCalls);
  }

  @Test
  void rejectsPrewarmBudgetOverflowBeforeLoadingSources() {
    var repository = new CountingRepository();
    assertThrows(
        IllegalArgumentException.class,
        () -> new CachedLexiconQueryService(repository, 4, Integer.MAX_VALUE, 1));
    assertEquals(0, repository.versionReads);
    assertEquals(0, repository.prewarmCalls);
  }

  @Test
  void emptyLookupIsImmutableZeroAccessAndInvalidKeysAreRejected() {
    var repository = new CountingRepository();
    var service = new CachedLexiconQueryService(repository, 4, 0, 0);
    int startupVersionReads = repository.versionReads;
    var empty = service.lookupForms(List.of());
    assertEquals(startupVersionReads, repository.versionReads);
    assertEquals(0, repository.queryCalls);
    assertEquals(0, empty.counts().queryKeys());
    assertEquals(false, empty.publishedVersion().isPresent());
    assertThrows(IllegalArgumentException.class, () -> service.lookupForms(List.of("Reliable")));
    assertThrows(
        IllegalArgumentException.class, () -> service.lookupForms(List.of("one two three four")));
  }

  @Test
  void deduplicatesKeysAndReportsColdMissThenPositiveAndNegativeHits() {
    var repository = new CountingRepository();
    repository.entries = List.of(entry(1, "reliable", "可靠"));
    var service = new CachedLexiconQueryService(repository, 8, 0, 0);
    var cold = service.lookupForms(List.of("reliable", "unknown", "reliable"));
    assertEquals(2, cold.counts().queryKeys());
    assertEquals(2, cold.counts().cacheMisses());
    assertEquals(1, cold.counts().dbBatches());
    var warm = service.lookupForms(List.of("reliable", "unknown"));
    assertEquals(1, warm.counts().positiveHits());
    assertEquals(1, warm.counts().negativeHits());
    assertEquals(0, warm.counts().cacheMisses());
    assertEquals(1, warm.candidates().size());
    assertThrows(UnsupportedOperationException.class, () -> warm.candidates().clear());
  }

  @Test
  void requestCountersExcludeConstructorWarmupAndIncludeVersionSwitchWarmup() {
    var repository = new CountingRepository();
    repository.prewarm = List.of(entry(1, "reliable", "可靠"));
    var service = new CachedLexiconQueryService(repository, 4, 1, 1);
    assertEquals(2, repository.prewarmCalls);
    var first = service.lookupForms(List.of("reliable"));
    assertEquals(0, first.counts().prewarmReads());
    repository.version = 2;
    repository.prewarm = List.of(entry(2, "reliable", "可靠"));
    var changed = service.lookupForms(List.of("reliable"));
    assertEquals(2, changed.counts().prewarmReads());
    assertEquals(1, changed.counts().versionReads());
  }

  @Test
  void largeBatchReturnsEveryLoadedCandidateEvenWhenCacheCapacityIsSmall() {
    var repository = new CountingRepository();
    repository.entries =
        List.of(entry(1, "one", "一"), entry(1, "two", "二"), entry(1, "three", "三"));
    var service = new CachedLexiconQueryService(repository, 1, 0, 0);
    var result = service.lookupForms(List.of("one", "two", "three"));
    assertEquals(
        java.util.Set.of("one", "two", "three"),
        result.candidates().stream()
            .map(LexiconHintCandidate::normalizedForm)
            .collect(java.util.stream.Collectors.toSet()));
  }

  @Test
  void preservesSnapshotOfCachedHitWhenNewMissEvictsItDuringSameBatch() {
    var repository = new CountingRepository();
    repository.entries = List.of(entry(1, "alpha", "甲"), entry(1, "beta", "乙"));
    var service = new CachedLexiconQueryService(repository, 1, 0, 0);
    service.lookupForms(List.of("alpha"));

    var result = service.lookupForms(List.of("alpha", "beta"));

    assertEquals(
        java.util.Set.of("alpha", "beta"),
        result.candidates().stream()
            .map(LexiconHintCandidate::normalizedForm)
            .collect(java.util.stream.Collectors.toSet()));
    assertEquals(1, result.counts().positiveHits());
    assertEquals(1, result.counts().cacheMisses());
  }

  @Test
  void evictsLeastRecentlyUsedEntryWhenCapacityIsExceeded() {
    var repository = new CountingRepository();
    var service = new CachedLexiconQueryService(repository, 1, 0, 0);

    service.lookupForms(List.of("reliable")).candidates();
    service.lookupForms(List.of("context")).candidates();
    service.lookupForms(List.of("reliable")).candidates();

    assertEquals(3, repository.queryCalls);
  }

  @Test
  void returnsLongerPhraseBeforeItsContainedWord() {
    var repository = new CountingRepository();
    repository.entries = List.of(entry(1, "figure out", "理解"), entry(1, "figure", "数字"));
    var service = new CachedLexiconQueryService(repository, 10, 0, 0);

    assertEquals(
        List.of("figure out", "figure"),
        service.lookupForms(List.of("figure", "figure out", "out")).candidates().stream()
            .map(LexiconHintCandidate::normalizedForm)
            .toList());
    assertEquals(1, repository.queryCalls);
  }

  @Test
  void enumeratesOnlyContiguousFormsOfAtMostThreeWords() {
    var repository = new CountingRepository();
    repository.entries =
        List.of(entry(1, "stream of data", "数据流"), entry(1, "a stream of data", "数据流"));
    var service = new CachedLexiconQueryService(repository, 100, 0, 0);

    assertEquals(
        List.of("stream of data"),
        service
            .lookupForms(
                List.of(
                    "a",
                    "a stream",
                    "a stream of",
                    "stream",
                    "stream of",
                    "stream of data",
                    "of",
                    "of data",
                    "data"))
            .candidates()
            .stream()
            .map(LexiconHintCandidate::normalizedForm)
            .toList());
    assertEquals(9, repository.lastQueriedForms.size());
    assertEquals(false, repository.lastQueriedForms.contains("a stream of data"));
    assertEquals(true, repository.lastQueriedForms.contains("stream of data"));
  }

  @Test
  void clearsCachedEntriesWhenPublishedVersionChanges() {
    var repository = new MutableRepository();
    var service = new CachedLexiconQueryService(repository, 10, 0, 0);

    assertEquals(
        "旧释义", service.lookupForms(List.of("reliable")).candidates().getFirst().finalGloss());
    repository.version = 2;
    repository.entry = entry(2, "新释义");

    assertEquals(
        "新释义", service.lookupForms(List.of("reliable")).candidates().getFirst().finalGloss());
  }

  @Test
  void prewarmsPositiveAndNegativeFormsWithoutDroppingAmbiguity() {
    var repository = new CountingRepository();
    var hint = entry(1, "bank", "银行");
    var block =
        new LexiconHintCandidate(
            UUID.randomUUID(),
            null,
            1,
            "en",
            "bank",
            "bank",
            LexiconEntryKind.WORD,
            LexiconHintAction.BLOCK,
            null,
            0,
            0,
            0);
    repository.prewarm = List.of(hint, block);
    var service = new CachedLexiconQueryService(repository, 4, 1, 1);

    assertEquals(2, service.lookupForms(List.of("bank")).candidates().size());
    assertEquals(0, repository.queryCalls);
    assertEquals(2, service.lookupForms(List.of("bank")).candidates().size());
  }

  @Test
  void failedPositiveBatchKeepsNegativeAndDoesNotRetrySameVersion() {
    var calls = new int[2];
    var version = new long[] {1};
    var changes = new java.util.ArrayList<Long>();
    var repo =
        new LexiconReadRepository() {
          @Override
          public long publishedVersion() {
            return version[0];
          }

          @Override
          public List<LexiconHintCandidate> findByForms(long v, Collection<String> forms) {
            return List.of();
          }

          @Override
          public List<LexiconHintCandidate> findPrewarmForms(
              long v, LexiconHintAction action, int limit) {
            if (action == LexiconHintAction.HINT) {
              calls[0]++;
              throw new IllegalStateException("synthetic failed batch");
            }
            calls[1]++;
            return List.of(
                new LexiconHintCandidate(
                    UUID.randomUUID(),
                    null,
                    v,
                    "en",
                    "blocked",
                    "blocked",
                    LexiconEntryKind.WORD,
                    LexiconHintAction.BLOCK,
                    null,
                    0,
                    0,
                    0));
          }
        };
    var service =
        new CachedLexiconQueryService(
            repo,
            4,
            1,
            1,
            (oldVersion, newVersion, positive, negative, duration) -> changes.add(newVersion));
    assertEquals(true, service.warmupStatus().degraded());
    assertEquals(2, service.warmupStatus().attempts());
    assertEquals(0, service.warmupStatus().positiveKeys());
    assertEquals(1, service.warmupStatus().negativeKeys());
    assertEquals(0, service.lookupForms(List.of("blocked")).counts().cacheMisses());
    service.refresh();
    assertEquals(1, calls[0]);
    assertEquals(1, calls[1]);
    assertEquals(0, changes.size());
    version[0] = 2;
    service.refresh();
    assertEquals(List.of(2L), changes);
    service.refresh();
    assertEquals(List.of(2L), changes);
    assertEquals(2, calls[0]);
    assertEquals(2, calls[1]);
  }

  private static LexiconHintCandidate entry(long version, String gloss) {
    return entry(version, "reliable", gloss);
  }

  private static LexiconHintCandidate entry(long version, String lemma, String gloss) {
    return new LexiconHintCandidate(
        UUID.nameUUIDFromBytes(
            ("entry:en:" + lemma).getBytes(java.nio.charset.StandardCharsets.UTF_8)),
        UUID.nameUUIDFromBytes(
            ("sense" + version + ":" + lemma).getBytes(java.nio.charset.StandardCharsets.UTF_8)),
        version,
        "en",
        lemma,
        lemma,
        lemma.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        LexiconHintAction.HINT,
        gloss,
        100,
        5.0,
        1);
  }

  private static final class MutableRepository implements LexiconReadRepository {
    private long version = 1;
    private LexiconHintCandidate entry = entry(1, "旧释义");

    @Override
    public long publishedVersion() {
      return version;
    }

    @Override
    public List<LexiconHintCandidate> findByForms(long requestedVersion, Collection<String> forms) {
      return forms.contains("reliable") ? List.of(entry) : List.of();
    }

    @Override
    public List<LexiconHintCandidate> findPrewarmForms(
        long requestedVersion, LexiconHintAction action, int limit) {
      return List.of();
    }
  }

  private static final class CountingRepository implements LexiconReadRepository {
    private long version = 1;
    private int queryCalls;
    private int versionReads;
    private int prewarmCalls;
    private boolean failVersionRead;
    private List<String> lastQueriedForms = List.of();
    private List<LexiconHintCandidate> entries =
        List.of(entry(1, "旧释义"), entry(1, "context", "语境"));
    private List<LexiconHintCandidate> prewarm = List.of();

    @Override
    public long publishedVersion() {
      versionReads++;
      if (failVersionRead) throw new IllegalStateException("synthetic version read failure");
      return version;
    }

    @Override
    public List<LexiconHintCandidate> findByForms(long requestedVersion, Collection<String> forms) {
      queryCalls++;
      lastQueriedForms = List.copyOf(forms);
      return entries.stream().filter(entry -> forms.contains(entry.normalizedForm())).toList();
    }

    @Override
    public List<LexiconHintCandidate> findPrewarmForms(
        long requestedVersion, LexiconHintAction action, int limit) {
      prewarmCalls++;
      return prewarm.stream().filter(value -> value.finalAction() == action).toList();
    }
  }
}
