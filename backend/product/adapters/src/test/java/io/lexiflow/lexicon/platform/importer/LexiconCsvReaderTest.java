package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
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
    assertEquals("可靠的, 值得信赖", plan.getFirst().entry().senses().getFirst().chineseGloss());
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

  @Test
  void selectsFirstCandidateFromGenericCsvAndPreservesFullSourceGloss() throws Exception {
    var file = Files.createTempFile("lexiflow-lexicon-first", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("bank", "银行；河岸", "4.30", "toefl~CC-BY-4.0~bank")
            + "\n"
            + row("reliable", "可靠的", "4.30", "toefl~CC-BY-4.0~reliable"));
    var rows = new LexiconCsvReader().read(file);
    assertEquals("银行", rows.get(0).chineseGloss());
    assertEquals("银行；河岸", rows.get(0).sourceGloss());
    assertEquals("可靠的", rows.get(1).chineseGloss());
    assertEquals("可靠的", rows.get(1).sourceGloss());
  }

  @Test
  void emptyFirstCandidateStillBuildsABlockedPublicationPlan() throws Exception {
    var file = Files.createTempFile("lexiflow-empty-first", ".csv");
    Files.writeString(file, header() + "\n" + row("emptyfirst", "；银行", "4.30", ""));
    var rows = new LexiconCsvReader().read(file);
    assertEquals("", rows.getFirst().chineseGloss());
    assertEquals("；银行", rows.getFirst().sourceGloss());
    assertEquals(LexiconCsvReader.PREPARATION_POLICY, rows.getFirst().hintPolicyReference());
    var plan = LexiconImportPlan.prepare(rows, 1, "a".repeat(64), Instant.EPOCH);
    assertEquals(1, plan.size());
    assertEquals(
        "unsafe_default_candidate",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(
            rows.getFirst()));
  }

  @Test
  void malformedGlossRetainsSourceAndBuildsBlockedPlan() throws Exception {
    var file = Files.createTempFile("lexiflow-malformed-gloss", ".csv");
    Files.writeString(file, header() + "\n" + row("malformed", "银行；[残缺", "4.30", ""));
    var rows = new LexiconCsvReader().read(file);
    assertEquals("", rows.getFirst().chineseGloss());
    assertEquals("银行；[残缺", rows.getFirst().sourceGloss());
    assertEquals(1, LexiconImportPlan.prepare(rows, 1, "a".repeat(64), Instant.EPOCH).size());
    assertEquals(
        "unsafe_default_candidate",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(
            rows.getFirst()));
  }

  @Test
  void genericCsvDoesNotAcceptCallerSuppliedCuratedFlag() throws Exception {
    var file = Files.createTempFile("lexiflow-curated-forgery", ".csv");
    Files.writeString(
        file, header() + ",curated\n" + row("stream of data", "数据流", "", "") + ",true");
    assertThrows(IllegalArgumentException.class, () -> new LexiconCsvReader().read(file));
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
