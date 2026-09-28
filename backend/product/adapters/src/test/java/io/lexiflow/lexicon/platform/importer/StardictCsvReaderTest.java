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
    assertEquals(0, scan.deduplicatedRows());
    var reliable = rows.getFirst().row();
    assertEquals("可靠的", reliable.chineseGloss());
    assertEquals("可靠的, 可信赖的", reliable.sourceGloss());
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
  void freezesAllOxfordWordsIncludingMissingRankIndependentOfSourceOrder() throws Exception {
    var file = Files.createTempFile("basic-selection", ".csv");
    var sourceRows = new ArrayList<String>();
    for (int i = 0; i < 2010; i++) {
      // 使用纯字母的不同合成词，并列排名验证稳定排序。
      String word =
          "word" + (char) ('a' + i / 676) + (char) ('a' + i / 26 % 26) + (char) ('a' + i % 26);
      sourceRows.add(row(word, "词条", "", "", "100", "200", "", "1"));
    }
    sourceRows.add(row("false", "错误的", "", "", "2370", "2516", "", "1"));
    sourceRows.add(row("unknown", "未知", "", "", "0", "0", "", "1"));
    sourceRows.add(row("specialist", "专家", "", "", "1", "1", "", ""));
    sourceRows.add(row("a phrase", "短语", "", "", "1", "1", "", "1"));
    Files.writeString(file, header() + "\n" + String.join("\n", sourceRows));
    var reader = new StardictCsvReader();
    var first = reader.selectBasicVocabulary(file);
    assertEquals(2012, first.words().size());
    assertTrue(first.lemmas().contains("false"));
    assertEquals("unknown", first.words().getLast().lemma());
    assertEquals(0, first.words().getLast().rank());
    assertTrue(first.lemmas().contains("unknown"));
    assertFalse(first.lemmas().contains("specialist"));
    assertFalse(first.lemmas().contains("a phrase"));
    java.util.Collections.reverse(sourceRows);
    Files.writeString(file, header() + "\n" + String.join("\n", sourceRows));
    var reversed = reader.selectBasicVocabulary(file);
    assertEquals(first.words(), reversed.words());
    assertEquals(first.digest(), reversed.digest());
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    var scan = reader.read(file, records::add, reversed);
    assertEquals(2012, scan.basicRows());
    assertEquals(2012, records.stream().filter(r -> r.row().basicVocabulary()).count());
    var falseWord =
        records.stream()
            .filter(r -> r.row().lemma().equals("false"))
            .findFirst()
            .orElseThrow()
            .row();
    assertTrue(falseWord.sourceOxfordBasic());
    assertEquals(
        "basic_vocabulary",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(
            falseWord));
    assertTrue(
        records.stream().allMatch(r -> r.row().hintPolicyReference().contains(reversed.digest())));
  }

  @Test
  void retainsOxfordEvidenceFromMergedDerivedRowsRegardlessOfOrder() throws Exception {
    var file = Files.createTempFile("derived-oxford", ".csv");
    var sourceRows =
        new ArrayList<>(
            List.of(
                row("bacterium", "n. 细菌", "", "", "0", "0", "s:bacteria", ""),
                row("bacteria", "n. 细菌", "", "", "0", "0", "0:bacterium/1:s", "1"),
                row("false alarm", "误报", "", "", "0", "0", "", "1")));
    var reader = new StardictCsvReader();
    String digest = null;
    for (int order = 0; order < 2; order++) {
      Files.writeString(file, header() + "\n" + String.join("\n", sourceRows));
      var selection = reader.selectBasicVocabulary(file);
      assertEquals(java.util.Set.of("bacteria"), selection.lemmas());
      if (digest != null) assertEquals(digest, selection.digest());
      digest = selection.digest();
      var rows = new ArrayList<StardictCsvReader.SourceRecord>();
      var scan = reader.read(file, rows::add, selection);
      assertEquals(1, scan.derivedRows());
      assertEquals(1, scan.basicRows());
      var root =
          rows.stream()
              .map(StardictCsvReader.SourceRecord::row)
              .filter(row -> row.lemma().equals("bacterium"))
              .findFirst()
              .orElseThrow();
      assertFalse(root.sourceOxfordBasic());
      assertTrue(root.basicVocabulary());
      assertFalse(root.prewarmEligible());
      assertEquals(
          "basic_vocabulary",
          io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(root));
      assertFalse(
          rows.stream()
              .map(StardictCsvReader.SourceRecord::row)
              .filter(row -> row.lemma().equals("false alarm"))
              .findFirst()
              .orElseThrow()
              .basicVocabulary());
      java.util.Collections.reverse(sourceRows);
    }
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
    assertTrue(rows.getLast().row().sourceGloss().contains("平行四边形"));
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
            .allMatch(
                value ->
                    value
                        .row()
                        .hintPolicyReference()
                        .contains(StardictCsvReader.PREPARATION_POLICY)));

    Files.writeString(
        file, header() + "\n" + row("sustainability", "n. 绿色", "", "", "17705", "9092", "", ""));
    assertThrows(
        IllegalArgumentException.class, () -> new StardictCsvReader().read(file, ignored -> {}));
  }

  @Test
  void publishesTrustedAllBasicPhraseFromStreamAndRejectsChangedCuratedSource() throws Exception {
    var file = Files.createTempFile("curated-all-basic", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("stream", "流", "", "", "", "", "", "1")
            + "\n"
            + row("data", "数据", "", "", "", "", "", "1")
            + "\n"
            + row("stream of data", "un. 数据流", "", "", "", "", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    var phrase =
        rows.stream()
            .map(StardictCsvReader.SourceRecord::row)
            .filter(value -> value.lemma().equals("stream of data"))
            .findFirst()
            .orElseThrow();
    assertTrue(phrase.allBasicPhrase());
    assertTrue(phrase.curatedGloss());
    var prepared = io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(phrase);
    assertEquals("数据流", prepared.gloss());
    assertEquals("curated", prepared.decisiveRule());
    assertEquals(
        "6b5efcba7b09e2e81366c25812f5a2e05f8b0bc1fad220022183ddb7b8fb8a2d",
        StardictCsvReader.PREPARATION_POLICY.split("curated_sha256=", 2)[1]);

    Files.writeString(
        file, header() + "\n" + row("stream of data", "un. 数据之流", "", "", "", "", "", ""));
    assertThrows(
        IllegalArgumentException.class, () -> new StardictCsvReader().read(file, ignored -> {}));
  }

  @Test
  void selectsFirstCandidateAndPreservesFullSourceGloss() throws Exception {
    var file = Files.createTempFile("first-candidate", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("bank", "n. 银行, 河岸", "", "", "100", "200", "", "")
            + "\n"
            + row("river", "河岸；银行", "", "", "100", "200", "", "")
            + "\n"
            + row("reliable", "可靠的", "", "", "100", "200", "", "")
            + "\n"
            + row("noblock", "[金融] 银行；河岸", "", "", "100", "200", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    assertEquals("银行", rows.get(0).row().chineseGloss());
    assertEquals("银行, 河岸", rows.get(0).row().sourceGloss());
    assertEquals("河岸", rows.get(1).row().chineseGloss());
    assertEquals("河岸；银行", rows.get(1).row().sourceGloss());
    assertEquals("可靠的", rows.get(2).row().chineseGloss());
    assertEquals("银行", rows.get(3).row().chineseGloss());
    assertEquals("[金融] 银行；河岸", rows.get(3).row().sourceGloss());
  }

  @Test
  void doesNotFallbackToSecondCandidateWhenFirstIsInvalid() throws Exception {
    var file = Files.createTempFile("no-fallback", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("bank", "bank；银行", "", "", "100", "200", "", "")
            + "\n"
            + row("longfirst", "一".repeat(25) + "；短", "", "", "100", "200", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    assertEquals("bank", rows.get(0).row().chineseGloss());
    assertEquals("bank；银行", rows.get(0).row().sourceGloss());
    var longFirst = rows.get(1).row().chineseGloss();
    assertTrue(longFirst.length() > 24);
    assertEquals("一".repeat(25) + "；短", rows.get(1).row().sourceGloss());
  }

  @Test
  void preservesEmptyFirstCandidateWithoutFallback() throws Exception {
    var file = Files.createTempFile("empty-first", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("emptyfirst", "；银行", "", "", "100", "200", "", "")
            + "\n"
            + row("bracketempty", "[金融]；银行", "", "", "100", "200", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    assertEquals("", rows.get(0).row().chineseGloss());
    assertEquals("；银行", rows.get(0).row().sourceGloss());
    assertEquals("", rows.get(1).row().chineseGloss());
    assertEquals("[金融]；银行", rows.get(1).row().sourceGloss());
  }

  @Test
  void malformedGlossIsBlockedWithoutAbortingTheSource() throws Exception {
    var file = Files.createTempFile("malformed-gloss", ".csv");
    var malformed = "[领域]银行；[残缺";
    Files.writeString(
        file,
        header()
            + "\n"
            + row("malformed", malformed, "", "", "100", "200", "", "")
            + "\n"
            + row("reliable", "可靠的；可信的", "", "", "100", "200", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    assertEquals(2, rows.size());
    assertEquals("", rows.getFirst().row().chineseGloss());
    assertEquals(malformed, rows.getFirst().row().sourceGloss());
    assertEquals(
        "empty_first_candidate",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(
            rows.getFirst().row()));
    assertEquals("可靠的", rows.getLast().row().chineseGloss());
  }

  @Test
  void parsesStardictCandidatesRespectsBracketsAndDelimiters() {
    assertEquals(List.of("银行", "河岸"), StardictCsvReader.parseStardictCandidates("银行, 河岸"));
    assertEquals(List.of("银行", "河岸"), StardictCsvReader.parseStardictCandidates("银行；河岸"));
    assertEquals(List.of("银行", "河岸"), StardictCsvReader.parseStardictCandidates("[金融] 银行；河岸"));
    assertEquals(List.of("资料之流"), StardictCsvReader.parseStardictCandidates("[网络] 资料之流"));
    assertEquals(List.of("银行", "河岸"), StardictCsvReader.parseStardictCandidates("银行；河岸"));
    assertEquals(List.of("可靠（结果）"), StardictCsvReader.parseStardictCandidates("可靠（结果）"));
  }

  @Test
  void preservesEmptyFirstCandidateInParsing() {
    assertEquals(List.of("", "银行"), StardictCsvReader.parseStardictCandidates("；银行"));
    assertEquals(List.of("", "银行"), StardictCsvReader.parseStardictCandidates("[金融]；银行"));
    assertEquals(List.of("", "银行"), StardictCsvReader.parseStardictCandidates("；银行"));
  }

  @Test
  void doesNotSplitInsideBrackets() {
    assertEquals(
        List.of("银行（机构；类型）", "河岸"), StardictCsvReader.parseStardictCandidates("银行（机构；类型），河岸"));
    assertEquals(
        List.of("银行【金融；投资】", "河岸"), StardictCsvReader.parseStardictCandidates("银行【金融；投资】；河岸"));
  }

  @Test
  void rejectsMismatchedAndUnclosedBrackets() {
    assertThrows(
        IllegalArgumentException.class, () -> StardictCsvReader.parseStardictCandidates("银行（河岸]"));
    assertThrows(
        IllegalArgumentException.class, () -> StardictCsvReader.parseStardictCandidates("银行（河岸"));
    assertThrows(
        IllegalArgumentException.class, () -> StardictCsvReader.parseStardictCandidates("银行）河岸"));
    assertThrows(
        IllegalArgumentException.class, () -> StardictCsvReader.parseStardictCandidates("银行【河岸）"));
  }

  @Test
  void handlesSupplementaryPlaneCharactersWithoutCorruption() {
    var emoji = "𠮷";
    var result = StardictCsvReader.parseStardictCandidates(emoji + "；银行");
    assertEquals(List.of(emoji, "银行"), result);
    assertEquals(emoji, StardictCsvReader.parseFirstStardictCandidate(emoji + "；银行"));
  }

  @Test
  void emptyFirstCandidateReachesHintPreparationAsBlocked() throws Exception {
    var file = Files.createTempFile("empty-to-hint", ".csv");
    Files.writeString(
        file, header() + "\n" + row("emptyfirst", "；银行", "", "", "100", "200", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    var record = rows.getFirst();
    assertEquals("", record.row().chineseGloss());
    assertEquals("；银行", record.row().sourceGloss());
    var exclusion =
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(
            record.row());
    assertEquals("empty_first_candidate", exclusion);
  }

  @Test
  void marksAllBasicPhrasesUsingCompleteSourceSelectionNotFunctionWordsOnly() throws Exception {
    var file = Files.createTempFile("all-basic-phrase", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("give", "给予", "", "", "", "", "", "1")
            + "\n"
            + row("up", "向上", "", "", "", "", "", "1")
            + "\n"
            + row("give up", "放弃", "", "", "", "", "", "")
            + "\n"
            + row("give quasars", "给予类星体", "", "", "", "", "", ""));
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, rows::add);
    var phrase =
        rows.stream()
            .map(StardictCsvReader.SourceRecord::row)
            .filter(value -> value.lemma().equals("give up"))
            .findFirst()
            .orElseThrow();
    assertTrue(phrase.allBasicPhrase());
    assertFalse(phrase.basicVocabulary());
    assertEquals(
        "all_basic_phrase",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.exclusionReason(phrase));
    var mixed =
        rows.stream()
            .map(StardictCsvReader.SourceRecord::row)
            .filter(value -> value.lemma().equals("give quasars"))
            .findFirst()
            .orElseThrow();
    assertFalse(mixed.allBasicPhrase());
    assertFalse(rows.getFirst().row().allBasicPhrase());
    Files.delete(file);
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
