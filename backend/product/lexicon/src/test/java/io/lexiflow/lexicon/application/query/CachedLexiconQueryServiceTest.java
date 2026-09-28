package io.lexiflow.lexicon.application.query;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.port.LexiconRepository;
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

  private static final class MutableRepository implements LexiconRepository {
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

    @Override
    public long publish(LexiconImportRequest request) {
      throw new UnsupportedOperationException();
    }

    @Override
    public long publishStreaming(
        LexiconImportMetadata metadata,
        long sourceRowsTotal,
        long expectedEntries,
        LexiconImportRowSource source) {
      throw new UnsupportedOperationException();
    }
  }

  private static final class CountingRepository implements LexiconRepository {
    private long version = 1;
    private int queryCalls;
    private int versionReads;
    private int prewarmCalls;
    private List<String> lastQueriedForms = List.of();
    private List<LexiconHintCandidate> entries =
        List.of(entry(1, "旧释义"), entry(1, "context", "语境"));
    private List<LexiconHintCandidate> prewarm = List.of();

    @Override
    public long publishedVersion() {
      versionReads++;
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

    @Override
    public long publish(LexiconImportRequest request) {
      throw new UnsupportedOperationException();
    }

    @Override
    public long publishStreaming(
        LexiconImportMetadata metadata,
        long sourceRowsTotal,
        long expectedEntries,
        LexiconImportRowSource source) {
      throw new UnsupportedOperationException();
    }
  }
}
