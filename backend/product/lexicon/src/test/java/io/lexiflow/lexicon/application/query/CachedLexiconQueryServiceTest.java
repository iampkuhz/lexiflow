package io.lexiflow.lexicon.application.query;

import static org.junit.jupiter.api.Assertions.assertEquals;

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
  void evictsLeastRecentlyUsedEntryWhenCapacityIsExceeded() {
    var repository = new CountingRepository();
    var service = new CachedLexiconQueryService(repository, 1, 0, 0);

    service.candidatesFor("reliable");
    service.candidatesFor("context");
    service.candidatesFor("reliable");

    assertEquals(3, repository.queryCalls);
  }

  @Test
  void returnsLongerPhraseBeforeItsContainedWord() {
    var repository = new CountingRepository();
    repository.entries = List.of(entry(1, "figure out", "理解"), entry(1, "figure", "数字"));
    var service = new CachedLexiconQueryService(repository, 10, 0, 0);

    assertEquals(
        List.of("figure out", "figure"),
        service.candidatesFor("figure out").stream()
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
        service.candidatesFor("a stream of data").stream()
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

    assertEquals("旧释义", service.candidatesFor("reliable").getFirst().finalGloss());
    repository.version = 2;
    repository.entry = entry(2, "新释义");

    assertEquals("新释义", service.candidatesFor("reliable").getFirst().finalGloss());
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

    assertEquals(2, service.candidatesFor("bank").size());
    assertEquals(0, repository.queryCalls);
    assertEquals(2, service.candidatesFor("bank").size());
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
    private List<String> lastQueriedForms = List.of();
    private List<LexiconHintCandidate> entries =
        List.of(entry(1, "旧释义"), entry(1, "context", "语境"));
    private List<LexiconHintCandidate> prewarm = List.of();

    @Override
    public long publishedVersion() {
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
