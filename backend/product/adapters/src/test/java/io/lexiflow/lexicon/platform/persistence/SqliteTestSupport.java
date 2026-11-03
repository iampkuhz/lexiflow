package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.LexiconImportService;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.DriverManager;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

final class SqliteTestSupport {
  private SqliteTestSupport() {}

  static Path schemaFile() {
    return Path.of(System.getProperty("lexiflow.sqlite.schema.file"));
  }

  static byte[] schema() throws Exception {
    return Files.readAllBytes(schemaFile());
  }

  static Path newDatabase(Path dir) throws java.io.IOException {
    return dir.toRealPath().resolve("facts-" + UUID.randomUUID() + ".db");
  }

  static String url(Path file) {
    return "jdbc:sqlite:" + file.toAbsolutePath().normalize();
  }

  static void initialize(Path file) throws Exception {
    SqliteSchemaInitializer.initialize(url(file), schemaFile());
  }

  static long publish(Path file, LexiconImportRow... rows) throws Exception {
    try (var persistence = SqlitePersistence.open(url(file))) {
      var request = new LexiconImportRequest(List.of(rows), metadata("fixture-seed"));
      return new LexiconImportService(persistence.repository()).publish(request);
    }
  }

  static LexiconImportRow row(
      String lemma, String gloss, List<String> aliases, List<String> inflections) {
    return row(lemma, gloss, aliases, inflections, true);
  }

  static LexiconImportRow row(
      String lemma,
      String gloss,
      List<String> aliases,
      List<String> inflections,
      boolean prewarmEligible) {
    var source = new SourceReference("fixture", "MIT", "row-" + lemma);
    return new LexiconImportRow(
        lemma,
        java.util.Objects.requireNonNullElse(gloss, ""),
        "definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        prewarmEligible);
  }

  static LexiconImportRow blockedRow(
      String lemma, String gloss, List<String> aliases, List<String> inflections) {
    var source = new SourceReference("fixture", "MIT", "row-" + lemma);
    return new LexiconImportRow(
        lemma,
        gloss,
        "definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true,
        true,
        "fixture-block",
        gloss,
        null,
        null,
        List.of(),
        false,
        false,
        false);
  }

  static LexiconImportMetadata metadata(String input) {
    try {
      String digest =
          HexFormat.of()
              .formatHex(
                  MessageDigest.getInstance("SHA-256")
                      .digest(input.getBytes(StandardCharsets.UTF_8)));
      return new LexiconImportMetadata(
          digest, "fixture", "MIT", Instant.EPOCH, GenerationIdentityReader.forInput(digest));
    } catch (java.security.NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  static Path approval(Path dir) throws Exception {
    Path path = dir.toRealPath().resolve("approvals.json");
    Files.writeString(
        path,
        "{\"schemaVersion\":1,\"sources\":[{\"sourceId\":\"fixture\",\"sourceDigest\":\""
            + DatasetPackageCodec.sha256("fixture-seed".getBytes(StandardCharsets.UTF_8))
            + "\",\"licenseId\":\"MIT\",\"redistributionApproved\":true,\"evidenceUrl\":\"https://example.invalid/license\"}]}",
        StandardCharsets.UTF_8);
    return path;
  }

  static long scalar(Connection connection, String sql) throws Exception {
    try (var statement = connection.createStatement();
        var rows = statement.executeQuery(sql)) {
      if (!rows.next()) throw new IllegalStateException("no result");
      return rows.getLong(1);
    }
  }

  static void createEmpty(Path file) throws Exception {
    try (var connection = DriverManager.getConnection(url(file))) {
      if (connection.isClosed())
        throw new IllegalStateException("SQLite fixture connection is closed");
    }
  }
}
