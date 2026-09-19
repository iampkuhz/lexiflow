package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.LexiconImportPlan;
import io.lexiflow.lexicon.application.SourceReference;
import java.util.List;
import org.springframework.jdbc.core.simple.JdbcClient;

/** PostgreSQL 中词库来源证据的表级 DAO。 */
final class PostgresLexiconEvidenceDao implements LexiconEvidenceDao {
  private final JdbcClient jdbc;

  PostgresLexiconEvidenceDao(JdbcClient jdbc) {
    this.jdbc = jdbc;
  }

  @Override
  public void insertEvidence(List<LexiconImportPlan.PlannedEntry> entries, long version) {
    for (var planned : entries) {
      insert(planned, version, planned.row().dictionary(), "DICTIONARY");
      insert(planned, version, planned.row().frequency(), "FREQUENCY");
      for (var source : planned.row().complexLists()) {
        insert(planned, version, source, "COMPLEX_WORD_LIST");
      }
    }
  }

  private void insert(
      LexiconImportPlan.PlannedEntry planned,
      long version,
      SourceReference source,
      String evidenceKind) {
    jdbc.sql(
            "INSERT INTO lexicon_source_evidence (lexicon_entry_id, lexicon_version, source_id, license_id, source_record_ref, evidence_kind, evidence_value) "
                + "VALUES (:entryId, :version, :source, :license, :reference, :kind, :value)")
        .param("entryId", planned.entry().entryId())
        .param("version", version)
        .param("source", source.sourceId())
        .param("license", source.licenseId())
        .param("reference", source.recordReference())
        .param("kind", evidenceKind)
        .param(
            "value",
            evidenceKind.equals("COMPLEX_WORD_LIST") ? source.recordReference() : "present")
        .update();
  }
}
