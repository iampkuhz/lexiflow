package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ReviewedSourceDefaultsTest {
  @TempDir Path temporary;

  private Path fixture() throws Exception {
    var source = temporary.resolve("source.csv");
    try (var in = getClass().getResourceAsStream("/lexicon/source-quality-fixture.csv")) {
      Files.copy(in, source);
    }
    return source;
  }

  @Test
  void sourceBoundDefaultsAndIndependentAdjectiveRetainCompleteFacts() throws Exception {
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(fixture(), records::add);
    var expected =
        Map.ofEntries(
            Map.entry("that's", "那是"),
            Map.entry("whisk", "搅拌"),
            Map.entry("horizontal", "水平的"),
            Map.entry("crash", "猛撞"),
            Map.entry("anglican", "圣公会的"),
            Map.entry("ease", "减轻"),
            Map.entry("marbled", "有大理石纹的"),
            Map.entry("in addition", "另外"),
            Map.entry("swirl", "旋转"),
            Map.entry("crack", "裂开或使裂开"),
            Map.entry("startle", "使吃惊"),
            Map.entry("butter", "黄油"),
            Map.entry("christian", "基督教的"),
            Map.entry("southern", "南方的"),
            Map.entry("specific", "特定的"),
            Map.entry("tentative", "暂定的"),
            Map.entry("immortal", "不死的"),
            Map.entry("general", "一般的"),
            Map.entry("instrument", "乐器"),
            Map.entry("herb", "香草"),
            Map.entry("with ease", "轻松地"),
            Map.entry("at ease", "自在"));
    for (var r : records) {
      var row = r.row();
      if (expected.containsKey(row.lemma())) {
        assertEquals(expected.get(row.lemma()), row.chineseGloss());
        assertEquals(
            expected.get(row.lemma()),
            io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(row).gloss());
        assertTrue(row.curatedGloss());
        assertTrue(row.hintPolicyReference().contains(ReviewedSourceDefaults.digest()));
        assertTrue(row.hintPolicyReference().contains("https://"));
      }
      if (row.lemma().equals("marble")) {
        assertFalse(row.inflections().contains("marbled"));
        assertTrue(row.inflections().contains("marbles"));
        assertEquals("大理石", row.chineseGloss());
      }
      if (row.lemma().equals("marbled")) assertTrue(row.inflections().isEmpty());
      if (row.lemma().equals("ease")) assertTrue(row.sourceGloss().startsWith("安乐, 安逸, 悠闲；"));
    }
    assertEquals(24, records.size());
  }

  @Test
  void missingEcdictMusicMeaningHasExplicitProjectAttributionAndPreservesSourceFacts()
      throws Exception {
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(fixture(), records::add);
    var instrument =
        records.stream()
            .map(r -> r.row())
            .filter(r -> r.lemma().equals("instrument"))
            .findFirst()
            .orElseThrow();
    assertEquals("乐器", instrument.chineseGloss());
    assertFalse(instrument.sourceGloss().contains("乐器"));
    assertEquals("lexiflow-reviewed-source-defaults", instrument.dictionary().sourceId());
    assertEquals("MIT", instrument.dictionary().licenseId());
    assertTrue(instrument.dictionary().recordReference().contains("original short gloss"));
    assertTrue(
        instrument.dictionary().recordReference().contains("https://dictionary.cambridge.org/"));
    assertEquals(java.util.List.of("instruments"), instrument.inflections());
    assertEquals(1709L, instrument.sourceBncRank());
    assertEquals("ecdict-bnc-frq", instrument.frequency().sourceId());
    var herb =
        records.stream()
            .map(r -> r.row())
            .filter(r -> r.lemma().equals("herb"))
            .findFirst()
            .orElseThrow();
    assertEquals("香草", herb.chineseGloss());
    assertTrue(herb.sourceGloss().contains("药草"));
    assertEquals("ecdict-stardict", herb.dictionary().sourceId());
    assertEquals(java.util.List.of("herbs"), herb.inflections());
  }

  @Test
  void exactSourceAndRelationDriftFailClosedEvenWhenAnchorSurvives() throws Exception {
    var file = fixture();
    try (var reader = new StardictCsvReader.CsvRecordReader(Files.newBufferedReader(file))) {
      reader.next();
      java.util.List<String> row;
      while ((row = reader.next()) != null) {
        var value = ReviewedSourceDefaults.entries().get(row.getFirst());
        if (value == null) continue;
        var translation = row.get(1);
        var exchange = row.get(6);
        value.validate(translation, exchange);
        assertThrows(
            IllegalArgumentException.class, () -> value.validate(translation + ",其他义", exchange));
        assertThrows(
            IllegalArgumentException.class,
            () -> value.validate(translation, exchange + "/0:other"));
      }
    }
  }

  @Test
  void projectConstructionsAreUnrankedAndBindTheirOwnSourceInsteadOfEcdict() {
    var rows = ReviewedSourceDefaults.constructionRows();
    assertEquals(
        java.util.List.of("awful lot", "knob of butter", "splash of"),
        rows.stream().map(r -> r.lemma()).toList());
    assertEquals(
        java.util.List.of("大量", "一小块黄油", "少量"), rows.stream().map(r -> r.chineseGloss()).toList());
    for (var row : rows) {
      assertEquals("lexiflow-reviewed-source-defaults", row.dictionary().sourceId());
      assertEquals("MIT", row.dictionary().licenseId());
      org.junit.jupiter.api.Assertions.assertNull(row.sourceBncRank());
      assertEquals(0, row.priority().memoryPriority());
      assertFalse(row.prewarmEligible());
      assertEquals(
          row.chineseGloss(),
          io.lexiflow.lexicon.application.importing.policy.HintPreparation.prepare(row).gloss());
    }
  }

  @Test
  void malformedDuplicateUnsafeOrUnboundQualityDataCannotLoad() throws Exception {
    String input;
    try (var in = getClass().getResourceAsStream("/lexicon/reviewed-source-defaults.tsv")) {
      input = new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
    }
    assertEquals(25, ReviewedSourceDefaults.parse(input).size());
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ReviewedSourceDefaults.parse(
                input + input.lines().skip(1).findFirst().orElseThrow() + "\n"));
    assertThrows(
        IllegalArgumentException.class,
        () -> ReviewedSourceDefaults.parse(input.replace("圣公会的", "<script>")));
    assertThrows(
        IllegalArgumentException.class,
        () -> ReviewedSourceDefaults.parse(input.replace("\tfalse\t", "\tunknown\t")));
  }
}
