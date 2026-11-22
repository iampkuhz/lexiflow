package io.lexiflow.api.deployment;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.api.preferences.PreferencesSchemaCommand;
import io.lexiflow.lexicon.platform.persistence.SqliteSchemaInitializer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class StorageContractTest {
  @TempDir Path temporary;

  @Test
  void ignoresFormattingAndSeedRowsButNotConstraintsOrIndexes() throws Exception {
    var original = digest("CREATE TABLE tab (id INTEGER NOT NULL);");
    assertEquals(
        original,
        digest(
            "-- comment\ncreate   table tab(id integer not null);\n"
                + "INSERT INTO tab VALUES (42);"));
    assertNotEquals(original, digest("CREATE TABLE tab (id INTEGER);"));
    assertNotEquals(
        original, digest("CREATE TABLE tab (id INTEGER NOT NULL); CREATE INDEX idx ON tab(id);"));
  }

  @Test
  void eachFileIsVerifiedIndependentlyWithoutAdoptingOrChangingData() throws Exception {
    var lexicon = temporary.resolve("lexicon.db");
    var prefs = temporary.resolve("preferences.db");
    try (var db = DriverManager.getConnection("jdbc:sqlite:" + lexicon);
        var sql = StorageContractTest.class.getResourceAsStream("/META-INF/lexiflow-schema.sql")) {
      SqliteSchemaInitializer.executeTrustedSql(db, sql.readAllBytes());
    }
    PreferencesSchemaCommand.run(new String[] {"jdbc:sqlite:" + prefs});
    StorageContract.verify("lexicon", lexicon);
    StorageContract.verify("preferences", prefs);
    try (var db = DriverManager.getConnection("jdbc:sqlite:" + lexicon);
        var mutation = db.createStatement()) {
      mutation.execute("CREATE TABLE extra (value TEXT)");
    }
    var lexiconBefore = Files.readAllBytes(lexicon);
    assertThrows(IllegalStateException.class, () -> StorageContract.verify("lexicon", lexicon));
    assertArrayEquals(lexiconBefore, Files.readAllBytes(lexicon));
    assertDoesNotThrow(() -> StorageContract.verify("preferences", prefs));
    try (var db = DriverManager.getConnection("jdbc:sqlite:" + prefs);
        var mutation = db.createStatement()) {
      mutation.execute("PRAGMA application_id=0");
    }
    var prefsBefore = Files.readAllBytes(prefs);
    assertThrows(java.sql.SQLException.class, () -> StorageContract.verify("preferences", prefs));
    assertArrayEquals(prefsBefore, Files.readAllBytes(prefs));
  }

  private static String digest(String sql) throws Exception {
    return StorageContract.expected(sql.getBytes(StandardCharsets.UTF_8));
  }
}
