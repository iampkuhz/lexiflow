package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import java.sql.Timestamp;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;

/** PostgreSQL 中词库导入批次与发布状态的表级 DAO。 */
final class PostgresLexiconImportBatchDao implements LexiconImportBatchDao {
  private final JdbcClient jdbc;

  PostgresLexiconImportBatchDao(JdbcClient jdbc) {
    this.jdbc = jdbc;
  }

  @Override
  public void lockPublication() {
    jdbc.sql("SET LOCAL lock_timeout = '5s'").update();
    jdbc.sql("LOCK TABLE lexicon_import_batch IN EXCLUSIVE MODE").update();
  }

  @Override
  public long nextVersion() {
    return jdbc.sql("SELECT COALESCE(MAX(lexicon_version), 0) + 1 FROM lexicon_import_batch")
        .query(Long.class)
        .single();
  }

  @Override
  public long publishedVersion() {
    return jdbc.sql("SELECT lexicon_version FROM lexicon_import_batch WHERE state = 'PUBLISHED'")
        .query(Long.class)
        .optional()
        .orElse(0L);
  }

  @Override
  public void insertStaged(
      UUID batchId, long version, LexiconImportMetadata metadata, int entryCount) {
    jdbc.sql(
            "INSERT INTO lexicon_import_batch (import_batch_id, lexicon_version, source_id, license_id, source_digest, acquired_at, state, entry_count) "
                + "VALUES (:id, :version, :source, :license, :digest, :acquired, 'STAGED', :entryCount)")
        .param("id", batchId)
        .param("version", version)
        .param("source", metadata.sourceId())
        .param("license", metadata.licenseId())
        .param("digest", metadata.sourceDigest())
        .param("acquired", Timestamp.from(metadata.acquiredAt()))
        .param("entryCount", entryCount)
        .update();
  }

  @Override
  public LexiconImportBatchDO findStaged(LexiconImportMetadata metadata) {
    return jdbc.sql(
            "SELECT import_batch_id, lexicon_version, source_rows_processed FROM lexicon_import_batch "
                + "WHERE source_digest = :digest AND source_id = :source AND license_id = :license AND state = 'STAGED'")
        .param("digest", metadata.sourceDigest())
        .param("source", metadata.sourceId())
        .param("license", metadata.licenseId())
        .query(
            (resultSet, rowNumber) ->
                new LexiconImportBatchDO(
                    resultSet.getObject(1, UUID.class), resultSet.getLong(2), resultSet.getLong(3)))
        .optional()
        .orElse(null);
  }

  @Override
  public void updateProcessed(UUID batchId, long processedThrough) {
    var updated =
        jdbc.sql(
                "UPDATE lexicon_import_batch SET source_rows_processed = :processed "
                    + "WHERE import_batch_id = :id AND state = 'STAGED'")
            .param("processed", processedThrough)
            .param("id", batchId)
            .update();
    if (updated != 1) {
      throw new IllegalStateException("StarDict batch is no longer staged");
    }
  }

  @Override
  public void completeStaged(
      UUID batchId, long version, long sourceRowsTotal, long expectedProcessed, long entryCount) {
    var updated =
        jdbc.sql(
                "UPDATE lexicon_import_batch SET entry_count = :entryCount, source_rows_total = :total "
                    + "WHERE import_batch_id = :id AND state = 'STAGED' AND lexicon_version = :version "
                    + "AND source_rows_processed = :processed")
            .param("entryCount", entryCount)
            .param("total", sourceRowsTotal)
            .param("id", batchId)
            .param("version", version)
            .param("processed", expectedProcessed)
            .update();
    if (updated != 1) {
      throw new IllegalStateException(
          "StarDict batch cannot be published before its full source is staged");
    }
  }

  @Override
  public void supersedePublished() {
    jdbc.sql("UPDATE lexicon_import_batch SET state = 'SUPERSEDED' WHERE state = 'PUBLISHED'")
        .update();
  }

  @Override
  public void publish(UUID batchId) {
    var updated =
        jdbc.sql(
                "UPDATE lexicon_import_batch SET state = 'PUBLISHED', published_at = CURRENT_TIMESTAMP "
                    + "WHERE import_batch_id = :id AND state = 'STAGED'")
            .param("id", batchId)
            .update();
    if (updated != 1) {
      throw new IllegalStateException("lexicon import batch is no longer staged");
    }
  }
}
