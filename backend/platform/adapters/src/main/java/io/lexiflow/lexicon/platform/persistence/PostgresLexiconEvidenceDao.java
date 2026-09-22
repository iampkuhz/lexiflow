package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.LexiconImportPlan;
import io.lexiflow.lexicon.application.SourceReference;
import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;

/** PostgreSQL 中词库来源证据的表级 DAO。 */
final class PostgresLexiconEvidenceDao implements LexiconEvidenceDao {
  private final JdbcTemplate batchJdbc;

  PostgresLexiconEvidenceDao(JdbcTemplate batchJdbc) {
    this.batchJdbc = batchJdbc;
  }

  @Override
  public void insertEvidence(List<LexiconImportPlan.PlannedEntry> entries, long version) {
    var arguments = new ArrayList<Object[]>();
    for (var planned : entries) {
      add(arguments, planned, version, planned.row().dictionary(), "DICTIONARY");
      add(arguments, planned, version, planned.row().frequency(), "FREQUENCY");
      for (var source : planned.row().complexLists()) {
        add(arguments, planned, version, source, "COMPLEX_WORD_LIST");
      }
    }
    if (arguments.isEmpty()) {
      return;
    }
    batchJdbc.batchUpdate(
        "INSERT INTO lexicon_source_evidence (lexicon_entry_id, lexicon_version, source_id, license_id, source_record_ref, evidence_kind, evidence_value) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?)",
        arguments);
  }

  private static void add(
      List<Object[]> arguments,
      LexiconImportPlan.PlannedEntry planned,
      long version,
      SourceReference source,
      String evidenceKind) {
    arguments.add(
        new Object[] {
          planned.entry().entryId(),
          version,
          source.sourceId(),
          source.licenseId(),
          source.recordReference(),
          evidenceKind,
          evidenceKind.equals("COMPLEX_WORD_LIST") ? source.recordReference() : "present"
        });
  }
}
