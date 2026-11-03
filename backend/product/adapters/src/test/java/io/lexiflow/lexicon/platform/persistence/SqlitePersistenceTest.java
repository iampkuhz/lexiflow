package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SqlitePersistenceTest {
  @TempDir Path temporary;

  @Test
  void connectAppliesAndVerifiesRequiredPerConnectionPragmas() throws Exception {
    Path file = temporary.toRealPath().resolve("facts.db");
    try (var created = DriverManager.getConnection("jdbc:sqlite:" + file)) {
      assertFalse(created.isClosed());
    }

    try (var connection = SqlitePersistence.connect("jdbc:sqlite:" + file)) {
      assertEquals(1, pragma(connection, "foreign_keys"));
      assertEquals(5000, pragma(connection, "busy_timeout"));
      assertEquals(2, pragma(connection, "synchronous"));
      assertEquals("delete", textPragma(connection, "journal_mode"));
    }
  }

  @Test
  void connectAndOpenRefuseMissingDatabaseWithoutCreatingIt() throws Exception {
    Path missing = temporary.toRealPath().resolve("missing.db");
    String url = "jdbc:sqlite:" + missing;
    assertThrows(IllegalArgumentException.class, () -> SqlitePersistence.connect(url));
    assertThrows(IllegalArgumentException.class, () -> SqlitePersistence.open(url));
    assertFalse(Files.exists(missing));
  }

  @Test
  void readOnlyUrlDoesNotPermitWrites() throws Exception {
    Path file = temporary.toRealPath().resolve("readonly.db");
    try (var created = DriverManager.getConnection("jdbc:sqlite:" + file)) {
      assertFalse(created.isClosed());
    }
    String url = "jdbc:sqlite:file:" + file + "?mode=ro";
    try (var connection = SqlitePersistence.connect(url)) {
      assertThrows(
          Exception.class,
          () -> {
            try (var statement = connection.createStatement()) {
              statement.executeUpdate("CREATE TABLE forbidden(value TEXT)");
            }
          });
    }
  }

  @Test
  void bareReadonlyUrlIsNormalizedToRealUriAndNeverCreatesLiteralQuestionFile() throws Exception {
    Path file = temporary.toRealPath().resolve("bare-readonly.db");
    try (var created = DriverManager.getConnection("jdbc:sqlite:" + file);
        var statement = created.createStatement()) {
      statement.execute("CREATE TABLE sentinel(value TEXT) STRICT");
      statement.execute("INSERT INTO sentinel VALUES('present')");
    }
    String externalUrl = "jdbc:sqlite:" + file + "?mode=ro";
    assertEquals(
        "jdbc:sqlite:" + file.toUri().toASCIIString() + "?mode=ro",
        SqlitePersistence.driverUrl(externalUrl));
    try (var connection = SqlitePersistence.connect(externalUrl)) {
      try (var statement = connection.createStatement();
          var rows = statement.executeQuery("SELECT value FROM sentinel")) {
        assertEquals(true, rows.next());
        assertEquals("present", rows.getString(1));
      }
      assertThrows(
          Exception.class,
          () -> {
            try (var statement = connection.createStatement()) {
              statement.executeUpdate("CREATE TABLE forbidden(value TEXT)");
            }
          });
    }
    assertFalse(Files.exists(Path.of(file + "?mode=ro")));
  }

  private static int pragma(java.sql.Connection connection, String name) throws Exception {
    try (var statement = connection.createStatement();
        var result = statement.executeQuery("PRAGMA " + name)) {
      result.next();
      return result.getInt(1);
    }
  }

  private static String textPragma(java.sql.Connection connection, String name) throws Exception {
    try (var statement = connection.createStatement();
        var result = statement.executeQuery("PRAGMA " + name)) {
      result.next();
      return result.getString(1);
    }
  }
}
