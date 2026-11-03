package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Path;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 真实 catalog 定义必须精确匹配可信最新版 SQL，不接受同名弱化的结构。 */
@Tag("sqlite")
class SqliteDatasetVerifierIntegrationTest {
  @TempDir Path temporary;

  @Test
  void rejectsSameNamedIndexTriggerAndViewWithDifferentDefinitions() throws Exception {
    for (String mutation :
        new String[] {
          "DROP INDEX lexicon_form_entry_idx; CREATE INDEX lexicon_form_entry_idx ON lexicon_form(normalized_form)",
          "DROP TRIGGER lexicon_form_canonical_guard; CREATE TRIGGER lexicon_form_canonical_guard BEFORE INSERT ON lexicon_form BEGIN SELECT 1; END",
          "DROP VIEW lexicon_lookup; CREATE VIEW lexicon_lookup AS SELECT normalized_form,entry_id FROM lexicon_form"
        }) {
      Path file = SqliteTestSupport.newDatabase(temporary);
      SqliteTestSupport.initialize(file);
      try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(file))) {
        SqliteDatasetVerifier.verifyStructure(connection, SqliteTestSupport.schema());
        try (var statement = connection.createStatement()) {
          // 驱动的单次执行不会处理任意多语句脚本，故逐条提交变更。
          int boundary = mutation.indexOf(';');
          statement.execute(mutation.substring(0, boundary));
          statement.execute(mutation.substring(boundary + 1));
        }
        assertThrows(
            Exception.class,
            () -> SqliteDatasetVerifier.verifyStructure(connection, SqliteTestSupport.schema()));
      }
    }
  }
}
