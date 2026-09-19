package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.LexiconImportMetadata;
import io.lexiflow.lexicon.application.LexiconImportPlan;
import io.lexiflow.lexicon.application.LexiconImportRequest;
import io.lexiflow.lexicon.application.LexiconImportRow;
import io.lexiflow.lexicon.application.LexiconRepository;
import io.lexiflow.lexicon.application.StagedLexiconImport;
import io.lexiflow.lexicon.domain.LexiconEntry;
import java.util.Collection;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.transaction.support.TransactionTemplate;

/** 组合多个词库 DAO 的 Repository 实现，不泄漏 PostgreSQL 表访问给上层。 */
final class DefaultLexiconRepository implements LexiconRepository {
  private final LexiconEntryDao entryDao;
  private final LexiconImportBatchDao batchDao;
  private final LexiconEvidenceDao evidenceDao;
  private final LexiconEntryMapper mapper;
  private final TransactionTemplate transaction;

  DefaultLexiconRepository(
      LexiconEntryDao entryDao,
      LexiconImportBatchDao batchDao,
      LexiconEvidenceDao evidenceDao,
      LexiconEntryMapper mapper,
      TransactionTemplate transaction) {
    this.entryDao = Objects.requireNonNull(entryDao, "entryDao");
    this.batchDao = Objects.requireNonNull(batchDao, "batchDao");
    this.evidenceDao = Objects.requireNonNull(evidenceDao, "evidenceDao");
    this.mapper = Objects.requireNonNull(mapper, "mapper");
    this.transaction = Objects.requireNonNull(transaction, "transaction");
  }

  @Override
  public long publishedVersion() {
    return batchDao.publishedVersion();
  }

  @Override
  public List<LexiconEntry> findByForms(long version, Collection<String> forms) {
    return mapper.toModels(entryDao.findByForms(version, forms));
  }

  @Override
  public List<LexiconEntry> findPrewarmCandidates(long version, int limit) {
    return mapper.toModels(entryDao.findPrewarmCandidates(version, limit));
  }

  @Override
  public long publish(LexiconImportRequest request) {
    return transaction.execute(
        status -> {
          batchDao.lockPublication();
          var version = batchDao.nextVersion();
          var entries =
              LexiconImportPlan.prepare(
                  request.rows(),
                  version,
                  request.metadata().sourceDigest(),
                  request.metadata().acquiredAt());
          var batchId = UUID.randomUUID();
          batchDao.insertStaged(batchId, version, request.metadata(), entries.size());
          entryDao.insertEntries(entries, version);
          evidenceDao.insertEvidence(entries, version);
          batchDao.supersedePublished();
          batchDao.publish(batchId);
          return version;
        });
  }

  @Override
  public StagedLexiconImport openOrResume(LexiconImportMetadata metadata) {
    return transaction.execute(
        status -> {
          batchDao.lockPublication();
          var existing = batchDao.findStaged(metadata);
          if (existing != null) {
            return staged(existing);
          }
          var batch = new LexiconImportBatchDO(UUID.randomUUID(), batchDao.nextVersion(), 0);
          batchDao.insertStaged(batch.batchId(), batch.version(), metadata, 1);
          return staged(batch);
        });
  }

  @Override
  public void stage(
      StagedLexiconImport batch,
      List<LexiconImportRow> rows,
      long processedThrough,
      LexiconImportMetadata metadata) {
    if (processedThrough < batch.sourceRowsProcessed()) {
      throw new IllegalArgumentException("processedThrough must not move backwards");
    }
    transaction.executeWithoutResult(
        status -> {
          var entries =
              LexiconImportPlan.prepare(
                  rows, batch.version(), metadata.sourceDigest(), metadata.acquiredAt());
          entryDao.insertEntries(entries, batch.version());
          evidenceDao.insertEvidence(entries, batch.version());
          batchDao.updateProcessed(batch.batchId(), processedThrough);
        });
  }

  @Override
  public void publish(StagedLexiconImport batch, long sourceRowsTotal) {
    if (sourceRowsTotal < batch.sourceRowsProcessed()) {
      throw new IllegalArgumentException("sourceRowsTotal must not be before processed rows");
    }
    transaction.executeWithoutResult(
        status -> {
          batchDao.lockPublication();
          batchDao.completeStaged(
              batch.batchId(),
              batch.version(),
              sourceRowsTotal,
              sourceRowsTotal,
              entryDao.countEntries(batch.version()));
          batchDao.supersedePublished();
          batchDao.publish(batch.batchId());
        });
  }

  private static StagedLexiconImport staged(LexiconImportBatchDO batch) {
    return new StagedLexiconImport(batch.batchId(), batch.version(), batch.sourceRowsProcessed());
  }
}
