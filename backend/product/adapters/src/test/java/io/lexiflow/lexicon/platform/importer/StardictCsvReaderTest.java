package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class StardictCsvReaderTest {
  @Test
  void streamsQuotedMultilineRowsAndConsolidatesRootInflections() throws Exception {
    var file = Files.createTempFile("stardict", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row(
                "reliable",
                "a. 可靠的, 可信赖的\\n[网络] 可靠",
                "a. worthy of\\nreliance",
                "cet6 ky toefl ielts",
                "3271",
                "3504",
                "",
                "")
            + "\n"
            + row("run", "n. 跑", "definition", "gk", "208", "202", "i:running/s:runs", "")
            + "\n"
            + row("running", "n. 跑步", "definition", "", "0", "0", "0:run/1:i", "1"));

    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    var scan = new StardictCsvReader().read(file, rows::add);

    assertEquals(3, scan.sourceRows());
    assertEquals(2, scan.importableRows());
    assertEquals(1, scan.derivedRows());
    var reliable = rows.getFirst().row();
    assertEquals("可靠的, 可信赖的", reliable.chineseGloss());
    assertEquals("a. worthy of reliance", reliable.definition());
    assertEquals(4.49, reliable.priority().frequencyZipf());
    assertEquals(4, reliable.priority().complexListCount());
    assertEquals(930, reliable.priority().memoryPriority());
    assertTrue(reliable.prewarmEligible());
    assertEquals(List.of("running", "runs"), rows.get(1).row().inflections());
  }

  @Test
  void keepsMissingDefinitionsButNeverPrewarmsMissingRankOrOxfordEntries() throws Exception {
    var file = Files.createTempFile("stardict", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("obscure", "a. 模糊的", "", "gre", "0", "not-a-rank", "", "")
            + "\n"
            + row("ability", "n. 能力", "definition", "toefl", "946", "783", "s:abilities", "1"));

    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);

    assertEquals("", rows.getFirst().row().definition());
    assertEquals(0, rows.getFirst().row().priority().memoryPriority());
    assertFalse(rows.getFirst().row().prewarmEligible());
    assertEquals(List.of("abilities"), rows.get(1).row().inflections());
    assertFalse(rows.get(1).row().prewarmEligible());
  }

  @Test
  void freezesTop2000FromOxfordRankedWordsIndependentOfSourceOrder() throws Exception {
    var file = Files.createTempFile("basic-selection", ".csv");
    var sourceRows = new ArrayList<String>();
    for (int i = 0; i < 2010; i++) {
      // 使用纯字母的不同合成词，并列排名验证稳定排序。
      String word =
          "word" + (char) ('a' + i / 676) + (char) ('a' + i / 26 % 26) + (char) ('a' + i % 26);
      sourceRows.add(row(word, "词条", "", "", "100", "200", "", "1"));
    }
    sourceRows.add(row("unknown", "未知", "", "", "0", "0", "", "1"));
    sourceRows.add(row("specialist", "专家", "", "", "1", "1", "", ""));
    sourceRows.add(row("a phrase", "短语", "", "", "1", "1", "", "1"));
    Files.writeString(file, header() + "\n" + String.join("\n", sourceRows));
    var reader = new StardictCsvReader();
    var first = reader.selectBasicVocabulary(file);
    assertEquals(2000, first.words().size());
    assertFalse(first.lemmas().contains("unknown"));
    assertFalse(first.lemmas().contains("specialist"));
    assertFalse(first.lemmas().contains("a phrase"));
    java.util.Collections.reverse(sourceRows);
    Files.writeString(file, header() + "\n" + String.join("\n", sourceRows));
    var reversed = reader.selectBasicVocabulary(file);
    assertEquals(first.words(), reversed.words());
    assertEquals(first.digest(), reversed.digest());
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    var scan = reader.read(file, records::add, reversed);
    assertEquals(2000, scan.basicRows());
    assertEquals(2000, records.stream().filter(r -> r.row().basicVocabulary()).count());
    assertTrue(
        records.stream().allMatch(r -> r.row().hintPolicyReference().contains(reversed.digest())));
  }

  @Test
  void importsDuplicateGlossAndInheritedBasicInflectionsAsPublishedData() throws Exception {
    var file = Files.createTempFile("basic-and-gloss", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("ability", "n. 能力", "", "", "946", "783", "s:abilities", "1")
            + "\n"
            + row("parallelogram", "n. 平行四边形\\n[机] 平行四边形", "", "", "0", "0", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    var scan = new StardictCsvReader().read(file, rows::add);
    assertEquals(1, scan.selectedBasicLemmas());
    assertEquals(1, scan.basicRows());
    assertEquals(1, scan.deduplicatedRows());
    assertTrue(rows.getFirst().row().basicVocabulary());
    assertEquals(List.of("abilities"), rows.getFirst().row().inflections());
    assertFalse(rows.getLast().row().basicVocabulary());
    assertEquals("平行四边形", rows.getLast().row().chineseGloss());
  }

  @Test
  void publishesOnlyReviewedShortGlossesWhenTheExpectedSourceExpressionStillExists()
      throws Exception {
    var file = Files.createTempFile("curated-gloss", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("sustainability", "n. 持续性, 能维持性, 永续性", "", "", "17705", "9092", "", "")
            + "\n"
            + row("literally", "adv. 逐字地, 按照字面上地, 不夸张地", "", "", "3512", "2469", "", "")
            + "\n"
            + row("stream of data", "un. 数据流\\n[网络] 资料之流", "", "", "0", "0", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    assertEquals(
        List.of("可持续性", "按字面意思", "数据流"),
        rows.stream().map(value -> value.row().chineseGloss()).toList());
    assertTrue(rows.getFirst().row().sourceGloss().contains("持续性"));
    assertEquals(17705L, rows.getFirst().row().sourceBncRank());
    assertEquals(9092L, rows.getFirst().row().sourceFrqRank());
    assertTrue(
        rows.stream()
            .allMatch(value -> value.row().hintPolicyReference().contains("curated-gloss-v2")));

    Files.writeString(
        file, header() + "\n" + row("sustainability", "n. 绿色", "", "", "17705", "9092", "", ""));
    assertThrows(
        IllegalArgumentException.class, () -> new StardictCsvReader().read(file, ignored -> {}));
  }

  private static String header() {
    return String.join(
        ",",
        "word",
        "phonetic",
        "definition",
        "translation",
        "pos",
        "collins",
        "oxford",
        "tag",
        "bnc",
        "frq",
        "exchange",
        "detail",
        "audio");
  }

  private static String row(
      String word,
      String translation,
      String definition,
      String tag,
      String bnc,
      String frq,
      String exchange,
      String oxford) {
    return String.join(
        ",",
        quote(word),
        "",
        quote(definition),
        quote(translation),
        "",
        "",
        quote(oxford),
        quote(tag),
        quote(bnc),
        quote(frq),
        quote(exchange),
        "",
        "");
  }

  private static String quote(String value) {
    return '"' + value.replace("\"", "\"\"") + '"';
  }
}
