package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.LexiconImportPlan;
import java.nio.file.Files;
import java.time.Instant;
import org.junit.jupiter.api.Test;

class LexiconCsvReaderTest {
  @Test
  void readsQuotedGlossAndCalculatesNonPersonalPrewarmPriority() throws Exception {
    var file = Files.createTempFile("lexiflow-lexicon", ".csv");
    Files.writeString(
        file, header() + "\n" + row("reliable", "可靠的, 值得信赖", "4.30", "toefl~CC-BY-4.0~reliable"));

    var rows = new LexiconCsvReader().read(file);
    var plan = LexiconImportPlan.prepare(rows, 1, "a".repeat(64), Instant.EPOCH);

    assertEquals(1, plan.size());
    assertEquals("可靠的, 值得信赖", plan.getFirst().entry().chineseGloss());
    assertEquals(4.3, plan.getFirst().entry().priority().frequencyZipf());
    assertEquals(1, plan.getFirst().entry().priority().complexListCount());
    assertEquals(834, plan.getFirst().entry().priority().memoryPriority());
  }

  @Test
  void rejectsCrossRowAliasCollisionBeforePublication() throws Exception {
    var file = Files.createTempFile("lexiflow-lexicon", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("analyse", "分析", "4.20", "academic~CC-BY-4.0~analyse")
            + "\n"
            + row("analysis", "分析", "4.00", "academic~CC-BY-4.0~analysis")
                .replace(",,,", ",analyse,,"));

    var rows = new LexiconCsvReader().read(file);

    assertThrows(
        IllegalArgumentException.class,
        () -> LexiconImportPlan.prepare(rows, 1, "a".repeat(64), Instant.EPOCH));
  }

  private static String header() {
    return String.join(",", LexiconCsvReader.REQUIRED_HEADERS);
  }

  private static String row(String lemma, String gloss, String zipf, String complexEvidence) {
    return String.join(
        ",",
        lemma,
        '"' + gloss.replace("\"", "\"\"") + '"',
        "definition",
        "",
        "",
        zipf,
        "wordfreq",
        "CC-BY-SA-4.0",
        lemma,
        complexEvidence,
        "ecdict",
        "MIT",
        lemma);
  }
}
