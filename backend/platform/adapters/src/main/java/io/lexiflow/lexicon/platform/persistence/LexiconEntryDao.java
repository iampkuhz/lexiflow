package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import java.util.Collection;
import java.util.List;
import java.util.Map;

/** 词条及其从属记录的表级访问合同，仅供 persistence Repository 使用。 */
interface LexiconEntryDao {
  List<LexiconEntryDO> findByForms(long version, Collection<String> forms);

  List<LexiconEntryDO> findPrewarmCandidates(long version, int limit);

  Map<String, String> findCanonicalOwners(long version, Collection<String> surfaces);

  void insertEntries(List<LexiconImportPlan.PlannedEntry> entries, long version);

  long countEntries(long version);
}
