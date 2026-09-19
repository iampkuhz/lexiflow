package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.LexiconImportMetadata;
import java.util.UUID;

/** 导入发布批次的表级访问合同，仅供 persistence Repository 使用。 */
interface LexiconImportBatchDao {
  void lockPublication();

  long nextVersion();

  long publishedVersion();

  void insertStaged(UUID batchId, long version, LexiconImportMetadata metadata, int entryCount);

  LexiconImportBatchDO findStaged(LexiconImportMetadata metadata);

  void updateProcessed(UUID batchId, long processedThrough);

  void completeStaged(
      UUID batchId, long version, long sourceRowsTotal, long expectedProcessed, long entryCount);

  void supersedePublished();

  void publish(UUID batchId);
}
