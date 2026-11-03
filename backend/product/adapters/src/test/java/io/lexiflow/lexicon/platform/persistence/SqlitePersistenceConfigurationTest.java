package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.zaxxer.hikari.HikariDataSource;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;

/** API 数据源只允许复用既有只读 SQLite 文件，且连接池有界。 */
class SqlitePersistenceConfigurationTest {
  @TempDir Path temporary;

  @Test
  void createsDeferredReadonlyBoundedPoolForExistingFile() throws Exception {
    Path file = temporary.toRealPath().resolve("api.db");
    try (var connection = DriverManager.getConnection("jdbc:sqlite:" + file)) {
      assertTrue(!connection.isClosed());
    }
    String url = "jdbc:sqlite:file:" + file + "?mode=ro";
    var context = configuredContext(Map.of("spring.datasource.url", url));
    try {
      try (var dataSource = context.getBean(HikariDataSource.class)) {
        assertEquals(1, dataSource.getMinimumIdle());
        assertEquals(4, dataSource.getMaximumPoolSize());
        assertEquals(SqlitePersistence.driverUrl(url), dataSource.getJdbcUrl());
        try (var connection = dataSource.getConnection()) {
          assertEquals(1, pragma(connection, "foreign_keys"));
          assertEquals(5000, pragma(connection, "busy_timeout"));
          assertEquals(2, pragma(connection, "synchronous"));
          assertEquals("delete", textPragma(connection, "journal_mode"));
          assertThrows(
              Exception.class,
              () -> {
                try (var statement = connection.createStatement()) {
                  statement.executeUpdate("CREATE TABLE forbidden(value TEXT)");
                }
              });
        }
        try (var connection = SqlitePersistence.connect(url)) {
          assertEquals(1, pragma(connection, "foreign_keys"));
          assertEquals(5000, pragma(connection, "busy_timeout"));
          assertEquals("delete", textPragma(connection, "journal_mode"));
        }
      }
    } finally {
      context.close();
    }
  }

  @Test
  void rejectsMissingDatabaseWriteModeAndCredentials() throws Exception {
    Path missing = temporary.toRealPath().resolve("missing.db");
    assertThrows(
        Exception.class,
        () ->
            configuredContext(
                Map.of("spring.datasource.url", "jdbc:sqlite:file:" + missing + "?mode=ro")));
    Path file = temporary.toRealPath().resolve("existing.db");
    try (var connection = DriverManager.getConnection("jdbc:sqlite:" + file)) {
      assertTrue(!connection.isClosed());
    }
    assertThrows(
        Exception.class,
        () -> configuredContext(Map.of("spring.datasource.url", "jdbc:sqlite:" + file)));
    assertThrows(
        Exception.class,
        () ->
            configuredContext(
                Map.of(
                    "spring.datasource.url",
                    "jdbc:sqlite:file:" + file + "?mode=ro",
                    "spring.datasource.password",
                    "not-accepted")));
  }

  @Test
  void doesNotCreateDataSourceWithoutDatabaseUrl() {
    try (var context = configuredContext(Map.of("spring.datasource.username", "ignored"))) {
      assertTrue(context.getBeansOfType(HikariDataSource.class).isEmpty());
    }
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

  private static AnnotationConfigApplicationContext configuredContext(
      Map<String, Object> properties) {
    var context = new AnnotationConfigApplicationContext();
    context
        .getEnvironment()
        .getPropertySources()
        .addFirst(new MapPropertySource("test", properties));
    context.register(SqlitePersistenceConfiguration.class);
    context.refresh();
    return context;
  }
}
