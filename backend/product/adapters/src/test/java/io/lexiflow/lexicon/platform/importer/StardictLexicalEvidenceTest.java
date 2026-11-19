package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class StardictLexicalEvidenceTest {
  @TempDir Path temporary;

  @Test
  void selectsDominantPosButNeverMakesChineseOrFallsThroughUnsafeCandidate() {
    var fine =
        StardictLexicalEvidence.select(
            "fine",
            "n. 罚款, 罚金, 晴天, 精细\\na. 好的, 晴朗的, 健康的, 细小的, 精细的\\nvt. 罚款, 精炼, 澄清\\nvi. 变清, 变细\\nadv. 很好\\n[计] 精细");
    assertNotNull(fine);
    assertEquals("好的", fine.gloss());
    assertTrue(fine.reference().contains("dominant-pos=a"));
    assertNull(StardictLexicalEvidence.select("file", "n. 档案\\nvi. 列队行进\\nvt. 归档"));
    assertNull(StardictLexicalEvidence.select("ultrasonic", "n. 超声波\\na. 超音速的"));
    assertNull(StardictLexicalEvidence.select("fine", "n. 罚款\\na. 好的"));

    assertNull(StardictLexicalEvidence.select("fine", "a. 好的\\nn. 罚款"));
    assertNull(StardictLexicalEvidence.select("fine", "n. 罚款\\na. 有...的, 好的"));
    assertNull(StardictLexicalEvidence.select("fine", "n. 罚款"));
    assertNull(StardictLexicalEvidence.select("present", "n. 现在\\na. 出席的\\nv. 呈现"));
    assertNull(StardictLexicalEvidence.select("madeupword", "n. 名词\\nv. 动词"));
    // 同一词性不凭单一历史最大 sense 强改中文，保留未消歧语境的边界。
    assertNull(StardictLexicalEvidence.select("accept", "vt. 接受, 相信"));
    assertNull(StardictLexicalEvidence.select("spinal", "n. 脊髓麻醉\\na. 脊柱的"));
  }

  @Test
  void retainsIndependentSensesButNotSharedSynsetPluralAliases() {
    assertTrue(StardictLexicalEvidence.independent("saw", "n. 锯子\\nv. 锯开", "0:see/1:p"));
    assertTrue(StardictLexicalEvidence.independent("found", "vt. 建立", "0:find/1:dp"));
    assertTrue(StardictLexicalEvidence.independent("left", "a. 左边的", "0:leave/1:dp"));
    assertFalse(StardictLexicalEvidence.independent("bacteria", "n. 细菌", "0:bacterium/1:s"));
    assertFalse(StardictLexicalEvidence.independent("saw", "see的过去式", "0:see/1:p"));
    assertFalse(StardictLexicalEvidence.independent("saw", "n. 锯子", "0:unverifiedroot/1:p"));
    assertFalse(StardictLexicalEvidence.independent("madeupword", "n. 合成词", "0:make/1:p"));
  }

  @Test
  void readerSeparatesIndependentWordAndRootFormsWithoutChangingSources() throws Exception {
    var input = temporary.resolve("source.csv");
    Files.writeString(
        input,
        "word,translation,oxford,tag,bnc,frq,exchange\n"
            + "find,\"v. 寻找\",1,,100,110,\"p:found/d:found/i:finding/3:finds\"\n"
            + "found,\"vt. 建立, 创立\\nfind的过去式和过去分词\",1,,2608,2664,\"0:find/1:dp/p:founded/d:founded/i:founding/3:founds\"\n"
            + "bacterium,\"n. 细菌\",,,0,0,s:bacteria\n"
            + "bacteria,\"n. 细菌\",1,,0,0,0:bacterium/1:s\n");
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    var scan = new StardictCsvReader().read(input, rows::add);
    assertEquals(1, scan.derivedRows());
    var found =
        rows.stream()
            .map(StardictCsvReader.SourceRecord::row)
            .filter(row -> row.lemma().equals("found"))
            .findFirst()
            .orElseThrow();
    assertEquals("建立", found.chineseGloss());
    assertEquals(List.of("founded", "founding", "founds"), found.inflections());
    assertTrue(found.sourceGloss().contains("find的过去式和过去分词"));
    assertEquals(2608L, found.sourceBncRank());
    var find = rows.getFirst().row();
    assertEquals(List.of("finding", "finds", "found"), find.inflections());
  }

  @Test
  void sourcePreparationAddsIndependentRowsButKeepsOriginalPhysicalEvidence() throws Exception {
    var prepared = temporary.resolve("prepared.csv");
    var full = temporary.resolve("full.csv");
    var output = temporary.resolve("new.csv");
    String header = "word,translation,oxford,tag,bnc,frq,exchange\n";
    Files.writeString(prepared, header + "see,\"v. 看见\",1,,10,20,p:saw\n");
    Files.writeString(
        full,
        header
            + "saw,\"n. 锯子\\nvt. 锯开\",1,,6947,7433,0:see/1:p\n"
            + "bacteria,\"n. 细菌\",1,,0,0,0:bacterium/1:s\n");
    byte[] before = Files.readAllBytes(prepared);
    StardictSourcePreparationMain.prepare(prepared, full, output);
    var rows = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(output, rows::add);
    assertEquals(List.of("see", "saw"), rows.stream().map(r -> r.row().lemma()).toList());
    assertTrue(java.util.Arrays.equals(before, Files.readAllBytes(prepared)));
    assertThrows(
        IllegalArgumentException.class,
        () -> StardictSourcePreparationMain.prepare(prepared, full, output));
  }

  @Test
  void projectionDeduplicatesSameSenseAndRejectsConflictingCountsOrPos() throws Exception {
    String one =
        "<LexicalEntry id='one' index='test'><Lemma writtenForm='test' partOfSpeech='n'/>"
            + "<Sense dc:identifier='test%1:00:00::' synset='omw-en-00000001-n'><Count>5</Count></Sense></LexicalEntry>";
    var input = temporary.resolve("wordnet.xml");
    Files.writeString(input, xml(one + one.replace("id='one'", "id='two'")));
    var entries = WordnetEvidenceMain.read(input);
    assertEquals(5L, entries.get("test").counts().get("n"));
    assertEquals(1, entries.get("test").synsets().size());
    Files.writeString(input, xml(one + one.replace("<Count>5", "<Count>6")));
    assertThrows(IllegalArgumentException.class, () -> WordnetEvidenceMain.read(input));
    Files.writeString(input, xml(one.replace("00000001-n", "00000001-v")));
    assertThrows(IllegalArgumentException.class, () -> WordnetEvidenceMain.read(input));
    Files.writeString(input, "<!DOCTYPE LexicalResource [<!ENTITY x 'unexpected'>]>" + xml(one));
    assertThrows(IllegalArgumentException.class, () -> WordnetEvidenceMain.read(input));
  }

  @Test
  void corruptOrDuplicateEvidenceNeverLoads() {
    var header = StardictLexicalEvidence.HEADER + "\n";
    assertThrows(IllegalArgumentException.class, () -> StardictLexicalEvidence.parse("wrong"));
    assertThrows(
        IllegalArgumentException.class,
        () -> StardictLexicalEvidence.parse(header + "test\t-1\t0\t0\t0\tn\tomw-en-00000001-n\n"));
    var row = "test\t5\t0\t0\t0\tn\tomw-en-00000001-n\n";
    assertThrows(
        IllegalArgumentException.class, () -> StardictLexicalEvidence.parse(header + row + row));
    assertEquals(147306, StardictLexicalEvidence.entries().size());
  }

  private static String xml(String entries) {
    return "<LexicalResource xmlns:dc='https://globalwordnet.github.io/schemas/dc/'>"
        + "<Lexicon id='omw-en' version='2.0' language='en'>"
        + entries
        + "</Lexicon></LexicalResource>";
  }
}
