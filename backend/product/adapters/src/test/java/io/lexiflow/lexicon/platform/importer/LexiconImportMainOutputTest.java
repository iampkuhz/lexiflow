package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistence;
import io.lexiflow.lexicon.platform.persistence.PostgresSchemaInitializer;
import io.lexiflow.observability.platform.LexiconEventObserver;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.List;
import java.util.UUID;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

class LexiconImportMainOutputTest {
  @Test
  void validateReportUsesChineseLabelsAndOneFieldPerLineWithoutSpaces() throws Exception {
    var input = Files.createTempFile("lexicon-validation", ".csv");
    Files.writeString(
        input,
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            + "reliable,,worthy of reliance,可靠的,,,1,cet6,1,1,,,\n");
    String report;
    try {
      report = captureValidationReport(input, System.out);
    } finally {
      Files.deleteIfExists(input);
    }
    assertTrue(report.startsWith("导入校验结果：PASS\n"), report);
    assertTrue(report.contains("来源行数（source_rows）：1\n"), report);
    assertTrue(report.contains("可导入词条数（entries）：1\n"), report);
    assertTrue(report.contains("归并派生词数（derived_merged）：0\n"), report);
    assertTrue(report.contains("来源文件SHA-256（source_sha256）："), report);
    assertFalse(Pattern.compile("\\s").matcher(report.replace("\n", "")).find(), report);
  }

  @Test
  void canonicalPreflightAndPrewarmRunWithoutDatabaseAndPrintApplicationOrdering()
      throws Exception {
    var input = Files.createTempFile("lexicon-canonical", ".csv");
    Files.writeString(
        input,
        "lemma,chinese_gloss,definition,aliases,inflections,frequency_zipf,frequency_source_id,frequency_license_id,frequency_ref,complex_evidence,dictionary_source_id,dictionary_license_id,dictionary_ref\n"
            + canonicalRow("beta", 4, "row1")
            + canonicalRow("alpha", 4, "row2"));
    try {
      var validation = capture(input, "validate", System.out);
      assertTrue(validation.contains("PASS format=lexiflow-lexicon-v1 entries=2"), validation);
      var prewarm = capture(input, "prewarm-report", System.out);
      assertEquals(List.of("alpha"), prewarm.lines().map(line -> line.split("\t")[3]).toList());
    } finally {
      Files.deleteIfExists(input);
    }
  }

  @Test
  void stardictPrewarmIsBoundedByApplicationAndDoesNotOpenDatabase() throws Exception {
    var input = Files.createTempFile("lexicon-stardict", ".csv");
    Files.writeString(
        input,
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            + "reliable,,worthy of reliance,可靠的,,1,0,cet6,10,10,,,\n"
            + "uncommon,,rare,罕见的,,,1,,100,100,,,\n");
    try {
      var validation = capture(input, "validate", System.out);
      assertTrue(validation.contains("导入校验结果：PASS"), validation);
      var prewarm = capture(input, "prewarm-report", System.out);
      assertEquals(1, prewarm.lines().filter(line -> line.contains("\t")).count());
    } finally {
      Files.deleteIfExists(input);
    }
  }

  @Test
  void rejectsChangedStardictSourceBeforeReportingValidationSuccess() throws Exception {
    var input = Files.createTempFile("lexicon-source-change", ".csv");
    var original =
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            + "quasar,,definition,类星体,,,0,cet6,20,20,,,\n";
    Files.writeString(input, original);
    try {
      assertThrows(
          IllegalStateException.class,
          () ->
              LexiconImportMain.execute(
                  new String[] {"validate", "--input", input.toString()},
                  message -> {
                    if (message.equals("选择基础词")) {
                      try {
                        Files.writeString(input, original.replace("类星体", "遥远星体"));
                      } catch (java.io.IOException exception) {
                        throw new java.io.UncheckedIOException(exception);
                      }
                    }
                  }));
    } finally {
      Files.deleteIfExists(input);
    }
  }

  @Test
  void explicitPublishReportsTypedSourceChangeOnceWhenStardictChangesAfterDigest()
      throws Exception {
    var input = Files.createTempFile("lexicon-publish-source-change", ".csv");
    var original =
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            + "quasar,,definition,类星体,,,0,cet6,20,20,,,\n";
    Files.writeString(input, original);
    var events = new java.util.ArrayList<LexiconImportObserver.Event>();
    try {
      assertThrows(
          IllegalStateException.class,
          () ->
              LexiconImportMain.execute(
                  new String[] {
                    "publish",
                    "--input",
                    input.toString(),
                    "--database-url",
                    "must-not-open",
                    "--batch-source-id",
                    "fixture",
                    "--batch-license-id",
                    "MIT"
                  },
                  message -> {
                    if (message.equals("选择基础词")) {
                      try {
                        Files.writeString(input, original.replace("类星体", "遥远星体"));
                      } catch (java.io.IOException exception) {
                        throw new java.io.UncheckedIOException(exception);
                      }
                    }
                  },
                  events::add));
      assertEquals(1, events.stream().filter(LexiconImportObserver.Event::terminal).count());
      assertEquals(
          LexiconImportObserver.Reason.SOURCE_CHANGED,
          events.stream()
              .filter(LexiconImportObserver.Event::terminal)
              .findFirst()
              .orElseThrow()
              .reason());
    } finally {
      Files.deleteIfExists(input);
    }
  }

  @Test
  void emptyPublishIsRejectedByApplicationBeforeOpeningDatabase() throws Exception {
    var input = Files.createTempFile("lexicon-empty-publication", ".csv");
    Files.writeString(
        input,
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n");
    try {
      var failure =
          assertThrows(
              IllegalArgumentException.class,
              () ->
                  LexiconImportMain.execute(
                      new String[] {
                        "publish",
                        "--input",
                        input.toString(),
                        "--database-url",
                        "must-not-be-opened",
                        "--batch-source-id",
                        "fixture",
                        "--batch-license-id",
                        "MIT"
                      },
                      ignored -> {}));
      assertTrue(failure.getMessage().contains("no importable entries"), failure.getMessage());
    } finally {
      Files.deleteIfExists(input);
    }
  }

  @Test
  @Tag("postgres")
  void bothFileFormatsPublishThroughCliAndApplicationIntoIsolatedPostgres() throws Exception {
    var admin = System.getProperty("lexiflow.postgres.test.jdbcUrl");
    assertTrue(admin != null && !admin.isBlank(), "isolated PostgreSQL URL is required");
    var schema = "lexiflow_cli_" + UUID.randomUUID().toString().replace("-", "");
    try (var connection = DriverManager.getConnection(admin);
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA \"" + schema + "\"");
    }
    var input = Files.createTempFile("lexicon-file-publication", ".csv");
    try {
      var jdbcUrl = admin + (admin.contains("?") ? "&" : "?") + "currentSchema=" + schema;
      PostgresSchemaInitializer.initialize(
          jdbcUrl, Path.of(System.getProperty("lexiflow.postgres.schema.file")));
      Files.writeString(
          input,
          "lemma,chinese_gloss,definition,aliases,inflections,frequency_zipf,frequency_source_id,frequency_license_id,frequency_ref,complex_evidence,dictionary_source_id,dictionary_license_id,dictionary_ref\n"
              + canonicalRow("quasar", 4, "fixture-row"));
      var arguments =
          new String[] {
            "publish",
            "--input",
            input.toString(),
            "--database-url",
            jdbcUrl,
            "--batch-source-id",
            "fixture-batch",
            "--batch-license-id",
            "MIT"
          };
      var canonicalEvents = new java.util.ArrayList<LexiconImportObserver.Event>();
      var canonicalJson = new java.util.ArrayList<String>();
      var canonicalAdapter =
          new LexiconEventObserver(
              new StructuredEventLogger((level, json) -> canonicalJson.add(json)));
      assertTrue(
          captureArguments(
                  arguments,
                  System.out,
                  event -> {
                    canonicalEvents.add(event);
                    canonicalAdapter.onEvent(event);
                  })
              .contains("published_version=1"));
      assertEquals(8, canonicalEvents.stream().filter(event -> !event.terminal()).count());
      assertEquals(
          1, canonicalEvents.stream().filter(LexiconImportObserver.Event::terminal).count());
      assertEquals(LexiconImportObserver.Reason.OK, canonicalEvents.getLast().reason());
      assertEquals(
          8, canonicalJson.stream().filter(line -> line.contains("lexicon.import.stage")).count());
      assertEquals(
          1,
          canonicalJson.stream().filter(line -> line.contains("lexicon.import.completed")).count());
      assertTrue(canonicalJson.getLast().contains("\"existing_safe\""), canonicalJson.toString());
      try (var persistence = PostgresPersistence.open(jdbcUrl)) {
        assertEquals(1, persistence.repository().publishedVersion());
        assertEquals(1, persistence.repository().findByForms(1, List.of("quasar")).size());
      }
      Files.writeString(
          input,
          "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
              + "nebula,,cloud,星云,,,0,cet6,90000,90000,,,\n");
      var stardictEvents = new java.util.ArrayList<LexiconImportObserver.Event>();
      var stardictJson = new java.util.ArrayList<String>();
      var stardictAdapter =
          new LexiconEventObserver(
              new StructuredEventLogger((level, json) -> stardictJson.add(json)));
      assertTrue(
          captureArguments(
                  arguments,
                  System.out,
                  event -> {
                    stardictEvents.add(event);
                    stardictAdapter.onEvent(event);
                  })
              .contains("published_version=2"));
      assertEquals(8, stardictEvents.stream().filter(event -> !event.terminal()).count());
      assertEquals(
          1, stardictEvents.stream().filter(LexiconImportObserver.Event::terminal).count());
      assertEquals(LexiconImportObserver.Reason.OK, stardictEvents.getLast().reason());
      assertEquals(
          8, stardictJson.stream().filter(line -> line.contains("lexicon.import.stage")).count());
      assertEquals(
          1,
          stardictJson.stream().filter(line -> line.contains("lexicon.import.completed")).count());
      assertTrue(stardictJson.getLast().contains("\"existing_safe\""), stardictJson.toString());
      try (var persistence = PostgresPersistence.open(jdbcUrl)) {
        assertEquals(2, persistence.repository().publishedVersion());
        assertEquals(1, persistence.repository().findByForms(2, List.of("nebula")).size());
        assertEquals(0, persistence.repository().findByForms(2, List.of("quasar")).size());
        assertEquals(0, persistence.repository().findByForms(1, List.of("nebula")).size());
      }
    } finally {
      Files.deleteIfExists(input);
      try (var connection = DriverManager.getConnection(admin);
          var statement = connection.createStatement()) {
        statement.execute("DROP SCHEMA IF EXISTS \"" + schema + "\" CASCADE");
      }
    }
  }

  private static String captureArguments(
      String[] arguments, PrintStream originalOut, LexiconImportObserver observer)
      throws Exception {
    var output = new ByteArrayOutputStream();
    try (var capture = new PrintStream(output, true, StandardCharsets.UTF_8)) {
      System.setOut(capture);
      LexiconImportMain.execute(arguments, ignored -> {}, observer);
    } finally {
      System.setOut(originalOut);
    }
    return output.toString(StandardCharsets.UTF_8);
  }

  private static String canonicalRow(String lemma, double zipf, String ref) {
    return lemma + ",释义,,, ," + zipf + ",frequency,MIT,rank,,dictionary,MIT," + ref + "\n";
  }

  private static String capture(Path input, String action, PrintStream originalOut)
      throws Exception {
    var output = new ByteArrayOutputStream();
    try (var capture = new PrintStream(output, true, StandardCharsets.UTF_8)) {
      System.setOut(capture);
      LexiconImportMain.main(new String[] {action, "--input", input.toString(), "--limit", "1"});
    } finally {
      System.setOut(originalOut);
    }
    return output.toString(StandardCharsets.UTF_8);
  }

  // originalOut 是调用者借出的标准输出；这里只恢复，不取得关闭它的所有权。
  private static String captureValidationReport(Path input, PrintStream originalOut)
      throws Exception {
    var output = new ByteArrayOutputStream();
    try (var capture = new PrintStream(output, true, StandardCharsets.UTF_8)) {
      System.setOut(capture);
      LexiconImportMain.main(new String[] {"validate", "--input", input.toString()});
    } finally {
      System.setOut(originalOut);
    }
    return output.toString(StandardCharsets.UTF_8);
  }
}
