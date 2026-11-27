package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
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
    assertEquals(3, scan.importableRows());
    assertEquals(0, scan.derivedRows());
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
    assertEquals(1, scan.basicRows());
    assertEquals(1, records.stream().filter(r -> r.row().basicVocabulary()).count());
    var falseWord =
        records.stream()
            .filter(r -> r.row().lemma().equals("false"))
            .findFirst()
            .orElseThrow()
            .row();
    assertTrue(falseWord.sourceOxfordBasic());
    assertFalse(falseWord.basicVocabulary());
    assertNull(
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(falseWord)
            .exclusionReason());
    assertTrue(
        records.stream().allMatch(r -> r.row().hintPolicyReference().contains(reversed.digest())));
    var unknownOxford =
        records.stream()
            .filter(r -> r.row().lemma().equals("unknown"))
            .findFirst()
            .orElseThrow()
            .row();
    assertTrue(unknownOxford.basicVocabulary());
    assertNull(unknownOxford.sourceBncRank());
    assertEquals(
        "basic_vocabulary",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(unknownOxford)
            .exclusionReason());
  }

  @Test
  void bncRankOverridesOxfordSelectionButFrqAndFunctionWordsDoNot() throws Exception {
    var file = Files.createTempFile("bnc-oxford-eligibility", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("ranktwelve", "十二", "", "", "1200", "1500", "", "1")
            + "\n"
            + row("unknownoxford", "未知词", "", "", "0", "0", "", "1")
            + "\n"
            + row("frqonlyoxford", "仅有FRQ", "", "", "0", "500", "", "1")
            + "\n"
            + row("that", "那个", "", "", "1200", "1500", "", "")
            + "\n"
            + row("rankedroot", "根词", "", "", "1200", "1500", "s:rankedform", "")
            + "\n"
            + row("rankedform", "词形", "", "", "0", "0", "0:rankedroot/1:s", "1"));
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, records::add);

    var rankedOxford = row(records, "ranktwelve");
    assertTrue(rankedOxford.sourceOxfordBasic());
    assertFalse(rankedOxford.basicVocabulary());
    assertEquals(1200L, rankedOxford.sourceBncRank());
    assertNull(
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(rankedOxford)
            .exclusionReason());

    var frqOnly = row(records, "frqonlyoxford");
    assertNull(frqOnly.sourceBncRank());
    assertEquals(500L, frqOnly.sourceFrqRank());
    assertTrue(frqOnly.basicVocabulary());
    assertEquals(
        "basic_vocabulary",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(frqOnly)
            .exclusionReason());

    var rankedFunctionWord = row(records, "that");
    assertEquals(1200L, rankedFunctionWord.sourceBncRank());
    assertTrue(rankedFunctionWord.basicVocabulary());
    assertEquals(
        "basic_vocabulary",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(rankedFunctionWord)
            .exclusionReason());

    var rootHitOnlyByOxfordInflection = row(records, "rankedroot");
    assertFalse(rootHitOnlyByOxfordInflection.sourceOxfordBasic());
    assertEquals(1200L, rootHitOnlyByOxfordInflection.sourceBncRank());
    assertFalse(rootHitOnlyByOxfordInflection.basicVocabulary());
    assertNull(
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(
                rootHitOnlyByOxfordInflection)
            .exclusionReason());
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
          io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(root)
              .exclusionReason());
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
    assertEquals(0, scan.basicRows());
    assertEquals(1, scan.deduplicatedRows());
    assertFalse(rows.getFirst().row().basicVocabulary());
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
        "37c7973d0361096fc5d6c22c08d836168793d3acfd3fc7404db384232edf1563",
        StardictCsvReader.PREPARATION_POLICY.split("curated_sha256=", 2)[1].split(";", 2)[0]);

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
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(
                rows.getFirst().row())
            .exclusionReason());
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
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(record.row())
            .exclusionReason();
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
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(phrase)
            .exclusionReason());
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

  @Test
  void restoresOnlySourceBoundIndependentGroundWithoutMixingPrototypeForms() throws Exception {
    var file = Files.createTempFile("independent-ground", ".csv");
    // 自编释义，不复制原始 CSV；只保留本修正必需核对的来源表达和关系。
    var ground = groundSourceRow("n. 土地, 合成释义\\nv. 合成动作", groundExchange());
    var grind =
        row(
            "grind",
            "n. 磨, 合成研磨义",
            "",
            "cet6 ky toefl ielts gre",
            "5538",
            "5372",
            "d:ground/p:ground/i:grinding/3:grinds/s:grinds",
            "");
    Files.writeString(file, header() + "\n" + grind + "\n" + ground);
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    var reader = new StardictCsvReader();
    var scan = reader.read(file, records::add);
    assertEquals(2, scan.importableRows());
    assertEquals(0, scan.derivedRows());
    var independent = row(records, "ground");
    var prototype = row(records, "grind");
    assertEquals("土地", independent.chineseGloss());
    assertEquals("磨", prototype.chineseGloss());
    assertEquals(455L, independent.sourceBncRank());
    assertEquals(5538L, prototype.sourceBncRank());
    assertEquals(List.of("grounded", "grounds"), independent.inflections());
    assertEquals(List.of("grinding", "grinds", "ground"), prototype.inflections());
    assertFalse(independent.inflections().contains("grounding"));
    assertTrue(independent.sourceOxfordBasic());
    assertFalse(independent.basicVocabulary());
    assertEquals(498, independent.priority().memoryPriority());
    assertEquals(988, prototype.priority().memoryPriority());
    assertEquals(
        3282121050795924845L,
        io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId(independent.lemma()));
    assertEquals(
        7211612317377414155L,
        io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId(prototype.lemma()));
    assertEquals(
        "土地",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(independent)
            .gloss());
    assertEquals(
        "磨",
        io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(prototype)
            .gloss());
    var selection = reader.selectBasicVocabulary(file);
    var digest = selection.digest();
    Files.writeString(file, header() + "\n" + ground + "\n" + grind);
    var reversed = new ArrayList<StardictCsvReader.SourceRecord>();
    reader.read(file, reversed::add);
    assertEquals(digest, reader.selectBasicVocabulary(file).digest());
    for (var lemma : List.of("ground", "grind")) {
      var first = row(records, lemma);
      var second = row(reversed, lemma);
      assertEquals(first.lemma(), second.lemma());
      assertEquals(first.chineseGloss(), second.chineseGloss());
      assertEquals(first.sourceGloss(), second.sourceGloss());
      assertEquals(first.inflections(), second.inflections());
      assertEquals(first.sourceBncRank(), second.sourceBncRank());
      assertEquals(first.priority(), second.priority());
    }
    Files.delete(file);
  }

  @Test
  void rejectsIndependentGroundSourceAndPrototypeDriftInsteadOfFallingBackToDerived()
      throws Exception {
    var file = Files.createTempFile("ground-source-drift", ".csv");
    var reader = new StardictCsvReader();
    for (var exchange :
        List.of(
            groundExchange().replace("0:grind", "0:other"),
            groundExchange().replace("1:dp", "1:i"),
            groundExchange().replace("s:grounds", "s:grounding"),
            groundExchange().replace("/0:grind", ""),
            groundExchange() + "/0:other",
            groundExchange() + "/s:grounds")) {
      Files.writeString(file, header() + "\n" + groundSourceRow("n. 土地, 合成义", exchange));
      assertThrows(IllegalArgumentException.class, () -> reader.read(file, ignored -> {}));
    }
    for (var translation : List.of("n. 合成义", "n. 磨, 土地", "")) {
      Files.writeString(file, header() + "\n" + groundSourceRow(translation, groundExchange()));
      assertThrows(IllegalArgumentException.class, () -> reader.read(file, ignored -> {}));
    }
    Files.delete(file);
  }

  @Test
  void distinguishesIndependentNounSensesFromSharedSynsetPluralsWithOxfordEvidence()
      throws Exception {
    var file = Files.createTempFile("pure-derived-oxford", ".csv");
    Files.writeString(
        file,
        header()
            + "\n"
            + row("run", "奔跑", "", "", "", "", "i:running", "")
            + "\n"
            + row("running", "n. 跑步, 连续运行", "", "", "", "", "0:run/1:i", "1")
            + "\n"
            + row("bacterium", "细菌", "", "", "", "", "s:bacteria", "")
            + "\n"
            + row("bacteria", "n. 合成细菌义", "", "", "", "", "0:bacterium/1:s", "1"));
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    var scan = new StardictCsvReader().read(file, records::add);
    assertEquals(1, scan.derivedRows());
    assertEquals(
        List.of("run", "running", "bacterium"),
        records.stream().map(r -> r.row().lemma()).toList());
    assertEquals("跑步", row(records, "running").chineseGloss());
    assertTrue(row(records, "running").inflections().isEmpty());
    assertTrue(row(records, "run").basicVocabulary());
    assertTrue(row(records, "bacterium").basicVocabulary());
    assertEquals(List.of("running"), row(records, "run").inflections());
    assertEquals(List.of("bacteria"), row(records, "bacterium").inflections());
    Files.delete(file);
  }

  @Test
  void bindsIndependentGroundAndReviewedDefaultsToPreparationIdentity() {
    assertEquals(
        "deterministic-preparation-bnc-ranked-oxford-and-bounded-cleaning-v10"
            + ";independent_ground_sha256="
            + "337151d8aa58dc9df174c54ee1ca42347e0f9041c0a78740383f23ded451b903"
            + ";curated_sha256="
            + "37c7973d0361096fc5d6c22c08d836168793d3acfd3fc7404db384232edf1563"
            + ";lexical_evidence_sha256="
            + StardictLexicalEvidence.digest()
            + ";reviewed_defaults_sha256="
            + ReviewedSourceDefaults.digest()
            + ";reviewed_pos_sha256="
            + StardictLexicalEvidence.reviewDigest(),
        StardictCsvReader.PREPARATION_POLICY);
  }

  private static String groundExchange() {
    return "s:grounds/d:grounded/p:grounded/0:grind/1:dp/i:grinding/3:grinds";
  }

  private static String groundSourceRow(String translation, String exchange) {
    return row(
        "ground",
        translation,
        "synthetic independent meaning",
        "ielts",
        "455",
        "522",
        exchange,
        "1");
  }

  @Test
  void acceptsCompactSevenColumnRowsWithEquivalentConversionAndRejectsInvalidHeaders()
      throws Exception {
    var full = Files.createTempFile("full-format", ".csv");
    var compact = Files.createTempFile("compact-format", ".csv");
    var fullHeader = header();
    var compactHeader = "word,translation,oxford,tag,bnc,frq,exchange";
    Files.writeString(
        full,
        fullHeader + "\n" + row("walk", "行走", "meaning", "cet4", "100", "200", "3:walks", "1"));
    Files.writeString(compact, compactHeader + "\n" + "walk,行走,1,cet4,100,200,3:walks\n");
    var fullRows = new ArrayList<StardictCsvReader.SourceRecord>();
    var compactRows = new ArrayList<StardictCsvReader.SourceRecord>();
    var reader = new StardictCsvReader();
    assertTrue(reader.matches(full));
    assertTrue(reader.matches(compact));
    reader.read(full, fullRows::add);
    reader.read(compact, compactRows::add);
    assertEquals(fullRows.getFirst().row().lemma(), compactRows.getFirst().row().lemma());
    assertEquals(
        fullRows.getFirst().row().chineseGloss(), compactRows.getFirst().row().chineseGloss());
    assertEquals(
        fullRows.getFirst().row().sourceBncRank(), compactRows.getFirst().row().sourceBncRank());
    assertEquals(
        fullRows.getFirst().row().inflections(), compactRows.getFirst().row().inflections());
    assertEquals(
        fullRows.getFirst().row().sourceFrqRank(), compactRows.getFirst().row().sourceFrqRank());
    assertEquals(
        fullRows.getFirst().row().basicVocabulary(),
        compactRows.getFirst().row().basicVocabulary());
    assertEquals(fullRows.getFirst().row().priority(), compactRows.getFirst().row().priority());
    assertEquals("", compactRows.getFirst().row().definition());
    for (var invalid :
        List.of(
            "word,translation,oxford,tag,bnc,frq",
            "translation,word,oxford,tag,bnc,frq,exchange",
            "word,translation,oxford,tag,bnc,frq,exchange,unknown",
            "word,translation,translation,oxford,tag,bnc,frq,exchange")) {
      Files.writeString(compact, invalid + "\n");
      assertFalse(reader.matches(compact));
      assertThrows(IllegalArgumentException.class, () -> reader.read(compact, ignored -> {}));
    }
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

  private static io.lexiflow.lexicon.application.importing.model.LexiconImportRow row(
      List<StardictCsvReader.SourceRecord> records, String lemma) {
    return records.stream()
        .map(StardictCsvReader.SourceRecord::row)
        .filter(candidate -> candidate.lemma().equals(lemma))
        .findFirst()
        .orElseThrow();
  }

  private static String quote(String value) {
    return '"' + value.replace("\"", "\"\"") + '"';
  }
}
