package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
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
