package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import java.util.List;

/** 词库来源证据的表级访问合同，仅供 persistence Repository 使用。 */
interface LexiconEvidenceDao {
  void insertEvidence(List<LexiconImportPlan.PlannedEntry> entries, long version);
}
