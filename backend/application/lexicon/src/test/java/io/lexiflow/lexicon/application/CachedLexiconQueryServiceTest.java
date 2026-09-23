package io.lexiflow.lexicon.application;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.lexicon.domain.LexiconEntry;
import io.lexiflow.lexicon.domain.LexiconEntryKind;
import io.lexiflow.lexicon.domain.LexiconPriority;
import io.lexiflow.lexicon.domain.LexiconProvenance;
import io.lexiflow.lexicon.domain.LexiconSense;
import java.time.Instant;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 验证缓存查询服务只使用 Repository 和领域模型。 */
class CachedLexiconQueryServiceTest {
  @Test
  void evictsLeastRecentlyUsedEntryWhenCapacityIsExceeded() {
    var repository = new CountingRepository();
    var service = new CachedLexiconQueryService(repository, 1, 0);

    service.candidatesFor("reliable");
    service.candidatesFor("context");
    service.candidatesFor("reliable");

    assertEquals(3, repository.queryCalls);
  }

  @Test
  void returnsLongerPhraseBeforeItsContainedWord() {
    var repository = new CountingRepository();
    repository.entries = List.of(entry(1, "figure out", "理解"), entry(1, "figure", "数字"));
    var service = new CachedLexiconQueryService(repository, 10, 0);

    assertEquals(
        List.of("figure out", "figure"),
        service.candidatesFor("figure out").stream().map(LexiconEntry::lemma).toList());
  }

  @Test
  void clearsCachedEntriesWhenPublishedVersionChanges() {
    var repository = new MutableRepository();
    var service = new CachedLexiconQueryService(repository, 10, 0);

    assertEquals(
        "旧释义", service.candidatesFor("reliable").getFirst().senses().getFirst().chineseGloss());
    repository.version = 2;
    repository.entry = entry(2, "新释义");

    assertEquals(
        "新释义", service.candidatesFor("reliable").getFirst().senses().getFirst().chineseGloss());
  }

  private static LexiconEntry entry(long version, String gloss) {
    return entry(version, "reliable", gloss);
  }

  private static LexiconEntry entry(long version, String lemma, String gloss) {
    return new LexiconEntry(
        UUID.nameUUIDFromBytes(
            ("entry" + version + ":" + lemma).getBytes(java.nio.charset.StandardCharsets.UTF_8)),
        version,
        "en",
        lemma.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        lemma,
        List.of(
            new LexiconSense(
                UUID.nameUUIDFromBytes(
                    ("sense" + version + ":" + lemma)
                        .getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                gloss,
                "definition",
                "fixture")),
        List.of(),
        List.of(),
        new LexiconProvenance("fixture", "MIT", "a".repeat(64), Instant.EPOCH),
        new LexiconPriority(5.0, 1, 100));
  }

  private static final class MutableRepository implements LexiconRepository {
    private long version = 1;
    private LexiconEntry entry = entry(1, "旧释义");

    @Override
    public long publishedVersion() {
      return version;
    }

    @Override
    public List<LexiconEntry> findByForms(long requestedVersion, Collection<String> forms) {
      return forms.contains("reliable") ? List.of(entry) : List.of();
    }

    @Override
    public List<LexiconEntry> findPrewarmCandidates(long requestedVersion, int limit) {
      return List.of();
    }

    @Override
    public long publish(LexiconImportRequest request) {
      throw new UnsupportedOperationException();
    }

    @Override
    public StagedLexiconImport openOrResume(LexiconImportMetadata metadata) {
      throw new UnsupportedOperationException();
    }

    @Override
    public void stage(
        StagedLexiconImport batch,
        List<LexiconImportRow> rows,
        long processedThrough,
        LexiconImportMetadata metadata) {
      throw new UnsupportedOperationException();
    }

    @Override
    public void publish(StagedLexiconImport batch, long sourceRowsTotal) {
      throw new UnsupportedOperationException();
    }
  }

  private static final class CountingRepository implements LexiconRepository {
    private long version = 1;
    private int queryCalls;
    private List<LexiconEntry> entries = List.of(entry(1, "旧释义"), entry(1, "context", "语境"));

    @Override
    public long publishedVersion() {
      return version;
    }

    @Override
    public List<LexiconEntry> findByForms(long requestedVersion, Collection<String> forms) {
      queryCalls++;
      return entries.stream().filter(entry -> forms.contains(entry.lemma())).toList();
    }

    @Override
    public List<LexiconEntry> findPrewarmCandidates(long requestedVersion, int limit) {
      return List.of();
    }

    @Override
    public long publish(LexiconImportRequest request) {
      throw new UnsupportedOperationException();
    }

    @Override
    public StagedLexiconImport openOrResume(LexiconImportMetadata metadata) {
      throw new UnsupportedOperationException();
    }

    @Override
    public void stage(
        StagedLexiconImport batch,
        List<LexiconImportRow> rows,
        long processedThrough,
        LexiconImportMetadata metadata) {
      throw new UnsupportedOperationException();
    }

    @Override
    public void publish(StagedLexiconImport batch, long sourceRowsTotal) {
      throw new UnsupportedOperationException();
    }
  }
}
