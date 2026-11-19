package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import io.lexiflow.enrichment.platform.preferences.SqlitePreferencesStore;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

@Tag("sqlite")
class SqlitePreferencesStoreTest {
  @TempDir Path temp;

  @Test
  void persistsExplicitChoicesAndDoesNotDependOnLexicon() throws Exception {
    Path db = temp.resolve("prefs.db");
    String jdbc = "jdbc:sqlite:" + db;
    assertEquals(0, PreferencesSchemaCommand.run(new String[] {jdbc}));
    var store = new SqlitePreferencesStore(jdbc);
    var app = new LocalPreferences(store);
    assertEquals(1500, app.read().commonWordCutoff());
    var id = Long.toString(io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId("cats"));
    app.setCutoff(1000);
    app.hide(id, "cats", "猫");
    app.hide(id, "cats", "猫");
    assertEquals(1, app.read().hidden().size());
    assertEquals(id, app.read().hidden().getFirst().entryId());
    app.restore(id);
    assertTrue(app.read().hidden().isEmpty());
    byte[] before = Files.readAllBytes(db);
    assertEquals(0, PreferencesSchemaCommand.run(new String[] {jdbc}));
    assertArrayEquals(before, Files.readAllBytes(db));
    var afterRestart = new SqlitePreferencesStore(jdbc).read();
    assertEquals(1000, afterRestart.commonWordCutoff());
    assertTrue(afterRestart.hidden().isEmpty());
  }
}
