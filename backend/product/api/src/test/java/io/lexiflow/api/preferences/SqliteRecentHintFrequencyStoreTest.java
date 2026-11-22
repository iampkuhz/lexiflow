package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.enrichment.platform.preferences.SqliteRecentHintFrequencyStore;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SqliteRecentHintFrequencyStoreTest {
  @TempDir Path temp;

  @Test
  void missingDatabaseIsNeverCreated() {
    var absent = temp.resolve("missing.db");
    var store = new SqliteRecentHintFrequencyStore("jdbc:sqlite:" + absent);
    assertThrows(
        IllegalStateException.class, () -> store.record(LocalDate.now(), java.util.List.of(1L)));
    assertFalse(java.nio.file.Files.exists(absent));
  }

  @Test
  void keepsEightDaysReadsSevenAndSurvivesRestartAndConcurrentAdds() throws Exception {
    String jdbc = "jdbc:sqlite:" + temp.resolve("p.db");
    PreferencesSchemaCommand.run(new String[] {jdbc});
    var store = new SqliteRecentHintFrequencyStore(jdbc);
    var today = LocalDate.of(2026, 11, 10);
    try (var c = java.sql.DriverManager.getConnection(jdbc);
        var s = c.createStatement()) {
      s.execute("INSERT INTO recent_hint_daily_count VALUES('2026-11-02',9,4)");
      s.execute("INSERT INTO recent_hint_daily_count VALUES('2026-11-03',9,5)");
      s.execute("INSERT INTO recent_hint_daily_count VALUES('2026-11-04',9,6)");
    }
    var tasks = new java.util.ArrayList<Callable<Void>>();
    for (int i = 0; i < 36; i++)
      tasks.add(
          () -> {
            store.record(today, java.util.List.of(9L));
            return null;
          });
    try (var pool = Executors.newFixedThreadPool(6)) {
      for (var f : pool.invokeAll(tasks)) f.get();
    }
    assertEquals(42L, new SqliteRecentHintFrequencyStore(jdbc).read(today).get(9L));
    store.prune(today);
    try (var c = java.sql.DriverManager.getConnection(jdbc);
        var s = c.createStatement();
        var r = s.executeQuery("SELECT day FROM recent_hint_daily_count ORDER BY day")) {
      assertTrue(r.next());
      assertEquals("2026-11-03", r.getString(1));
      assertTrue(r.next());
      assertEquals("2026-11-04", r.getString(1));
      assertTrue(r.next());
      assertEquals("2026-11-10", r.getString(1));
      assertFalse(r.next());
    }
  }
}
