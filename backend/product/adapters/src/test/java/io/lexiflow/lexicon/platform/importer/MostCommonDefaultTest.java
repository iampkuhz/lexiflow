package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 默认取通用受核候选，不从合成上下文或 source first 推断 most-common。 */
class MostCommonDefaultTest {
  @TempDir Path temporary;

  private record Fixture(
      String lemma, String source, String expected, String exchange, String cleaned) {}

  static final List<Fixture> FIXTURES =
      List.of(
          new Fixture(
              "plane",
              "n. 平面, 扁平物, 机翼, 飞机, 水准, 地位\\na. 平的, 平面的\\nvt. 将...刨平, 刨平, 掠过水面\\nvi. 翱翔, 乘飞机旅行, 刨掉",
              "飞机",
              "s:planes/i:planing/p:planed/d:planed/3:planes",
              "平面, 扁平物, 机翼, 飞机, 水准, 地位；平的, 平面的；将...刨平, 刨平, 掠过水面；翱翔, 乘飞机旅行, 刨掉"),
          new Fixture(
              "tissue",
              "n. 薄的织物, 薄纱, 棉纸, 组织, 一套\\n[化] 组织",
              "组织",
              "s:tissues/i:tissuing/p:tissued/3:tissues/d:tissued",
              "薄的织物, 薄纱, 棉纸, 组织, 一套；[化] 组织"),
          new Fixture(
              "equal",
              "n. 对手, 匹敌, 同辈\\na. 相等的, 平等的, 胜任的, 合适的, 平静的, 不相上下的\\nvt. 等于, 比得上\\n[计] 等长度编码",
              "相等的",
              "3:equals/s:equals/d:equalled/p:equalled/i:equalling",
              "对手, 匹敌, 同辈；相等的, 平等的, 胜任的, 合适的, 平静的, 不相上下的；等于, 比得上；[计] 等长度编码"),
          new Fixture(
              "nerve",
              "n. 精神, 勇气, 叶脉, 神经\\nvt. 鼓起勇气",
              "神经",
              "s:nerves/p:nerved/i:nerving/d:nerved/3:nerves",
              "精神, 勇气, 叶脉, 神经；鼓起勇气"),
          new Fixture("mostly", "adv. 大概, 大部分, 主要\\n[经] 大部份", "大部分", "", "大概, 大部分, 主要；[经] 大部份"),
          new Fixture(
              "cook",
              "n. 厨子, 厨师\\nvt. 烹调, 煮饭, 加热\\nvi. 在煮着",
              "烹调",
              "i:cooking/d:cooked/p:cooked/s:cooks/3:cooks",
              "厨子, 厨师；烹调, 煮饭, 加热；在煮着"),
          new Fixture(
              "wrap",
              "n. 外套, 围巾, 包裹物, 限制, 约束, 秘密, 换行\\nvt. 包装, 卷, 缠绕, 包, 裹, 覆盖, 遮蔽, 隐藏, 掩护\\nvi. 缠绕, 穿外衣, 包起来\\n[计] 换行",
              "包",
              "d:wrapped/p:wrapped/i:wrapping/s:wraps/3:wraps",
              "外套, 围巾, 包裹物, 限制, 约束, 秘密, 换行；包装, 卷, 缠绕, 包, 裹, 覆盖, 遮蔽, 隐藏, 掩护；缠绕, 穿外衣, 包起来；[计] 换行"),
          new Fixture(
              "spinal",
              "a. 针的, 尖刺的, 尖刺状突起的, 脊骨的\\n[医] 棘折; 脊柱的",
              "脊柱的",
              "",
              "针的, 尖刺的, 尖刺状突起的, 脊骨的；[医] 棘折; 脊柱的"));

  static final List<Fixture> REMAINING_FIXTURES =
      List.of(
          new Fixture(
              "attribute",
              "n. 属性, 标志, 定语\\nvt. 把...归于, 认为...属于\\n[计] 属性",
              "归因于",
              "d:attributed/s:attributes/p:attributed/3:attributes/i:attributing",
              "属性, 标志, 定语；把...归于, 认为...属于；[计] 属性"),
          new Fixture(
              "efficiently", "adv. 生效, 能胜任, 有能力, 效率高, 有效", "效率高", "", "生效, 能胜任, 有能力, 效率高, 有效"),
          new Fixture(
              "ease",
              "n. 安乐, 安逸, 悠闲\\nvt. 使安乐, 使安心, 减轻, 放松\\nvi. 减轻, 放松, 灵活地移动",
              "减轻",
              "p:eased/i:easing/d:eased/3:eases",
              "安乐, 安逸, 悠闲；使安乐, 使安心, 减轻, 放松；减轻, 放松, 灵活地移动"));

  private static List<Fixture> allFixtures() {
    var fixtures = new ArrayList<>(FIXTURES);
    fixtures.addAll(REMAINING_FIXTURES);
    return List.copyOf(fixtures);
  }

  @Test
  void selectsReviewedCandidateInBothSupportedContractsAndPreservesAllSourceCandidates()
      throws Exception {
    for (boolean compact : List.of(true, false)) {
      for (var f : allFixtures()) {
        var record = read(f.lemma(), f.source(), f.exchange(), compact).getFirst();
        var row = record.row();
        assertEquals(f.expected(), row.chineseGloss());
        assertEquals(f.cleaned(), row.sourceGloss());
        assertEquals(
            StardictCsvReader.parseStardictCandidates(f.cleaned()),
            StardictCsvReader.parseStardictCandidates(row.sourceGloss()));
        assertEquals(
            !List.of("attribute").contains(f.lemma()),
            StardictCsvReader.parseStardictCandidates(row.sourceGloss()).contains(f.expected()));
        assertTrue(
            StardictCsvReader.parseStardictCandidates(row.sourceGloss()).contains(sourceAnchor(f)));
        assertTrue(row.curatedGloss());
        assertEquals(f.expected(), HintPreparation.prepare(row).gloss());
        assertEquals("curated", HintPreparation.prepare(row).decisiveRule());
        assertEquals(3001L, row.sourceBncRank());
        assertEquals(4001L, row.sourceFrqRank());
        assertEquals(List.of(), row.aliases());
        assertTrue(row.hintPolicyReference().contains(StardictCsvReader.PREPARATION_POLICY));
      }
    }
  }

  @Test
  void
      failsClosedOnMissingChangedEmptyIllegalOrUnknownReviewedSourceEvenIfTargetSubstringSurvives() {
    for (var f : allFixtures()) {
      for (String changed :
          List.of(
              "",
              "；" + f.expected(),
              f.expected(),
              f.source() + ",合成新增义",
              f.source().replace(sourceAnchor(f), "合成替换"),
              f.source() + "(坏括号",
              "[未知]" + f.source(),
              f.source() + "<script>")) {
        assertThrows(
            IllegalArgumentException.class,
            () -> read(f.lemma(), changed, f.exchange(), true),
            f.lemma() + ":" + changed);
      }
    }
  }

  private static String sourceAnchor(Fixture fixture) {
    return switch (fixture.lemma()) {
      case "ease" -> "安乐";
      case "attribute" -> "把...归于";
      default -> fixture.expected();
    };
  }

  @Test
  void reviewedRawVerbAnchorNeverBecomesDisplayOrFallback() throws Exception {
    for (boolean compact : List.of(true, false)) {
      var f = REMAINING_FIXTURES.getFirst();
      var row = read(f.lemma(), f.source(), f.exchange(), compact).getFirst().row();
      assertTrue(StardictCsvReader.parseStardictCandidates(row.sourceGloss()).contains("把...归于"));
      assertFalse(
          io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation.safeGloss(
              "把...归于"));
      assertTrue(
          io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation.safeGloss(
              row.chineseGloss()));
      assertEquals("归因于", row.chineseGloss());
      assertEquals("归因于", HintPreparation.prepare(row).gloss());
      assertTrue(row.curatedGloss());
    }
  }

  @Test
  void displaySafetyAndIndependentReviewRemainMandatoryForRawAnchors() throws Exception {
    var type =
        java.util.Arrays.stream(StardictCsvReader.class.getDeclaredClasses())
            .filter(c -> c.getSimpleName().equals("CuratedGloss"))
            .findFirst()
            .orElseThrow();
    var constructor =
        type.getDeclaredConstructor(String.class, String.class, String.class, String.class);
    constructor.setAccessible(true);
    for (String unsafe :
        new String[] {
          null, "", "把...归于", "<script>归因于", "归因 于", "英".repeat(25), "english", "归因\u200b于"
        }) {
      var error =
          assertThrows(
              java.lang.reflect.InvocationTargetException.class,
              () -> constructor.newInstance("把...归于", unsafe, "0".repeat(64), "review"));
      assertTrue(error.getCause() instanceof IllegalArgumentException);
    }
    for (var values :
        List.of(
            List.of("", "归因于", "0".repeat(64), "review"),
            List.of("把...归于", "归因于", "0".repeat(64), ""))) {
      var error =
          assertThrows(
              java.lang.reflect.InvocationTargetException.class,
              () -> constructor.newInstance(values.toArray()));
      assertTrue(error.getCause() instanceof IllegalArgumentException);
    }
    var validate = type.getDeclaredMethod("validateSource", String.class, String.class);
    validate.setAccessible(true);
    for (var pair : List.of(List.of("其他合法候选", "把...归于"), List.of("把...归于(坏括号", "把...归于"))) {
      var source = pair.getFirst();
      var hash =
          java.util.HexFormat.of()
              .formatHex(
                  java.security.MessageDigest.getInstance("SHA-256")
                      .digest(source.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
      var curated = constructor.newInstance(pair.getLast(), "归因于", hash, "review");
      var error =
          assertThrows(
              java.lang.reflect.InvocationTargetException.class,
              () -> validate.invoke(curated, "fixture", source));
      assertTrue(error.getCause() instanceof IllegalArgumentException);
    }
  }

  @Test
  void unreviewedUnsafeFirstCandidateStillCannotReachDisplay() throws Exception {
    for (String source : List.of("vt. 把...归于", "n. 非法?", "n. 合成/词", "n. " + "英".repeat(25))) {
      var row = read("unreviewed", source, "", true).getFirst().row();
      assertFalse(row.curatedGloss());
      assertNull(HintPreparation.prepare(row).gloss());
    }
  }

  @Test
  void keepsUnreviewedDefaultsAndNeverUsesCookingContextAsSenseRank() throws Exception {
    for (var pair :
        List.of(
            List.of("bank", "银行,河岸", "银行"),
            List.of("fatty", "肥胖的,脂肪的", "肥胖的"),
            List.of("knob", "把手,球形突出物", "把手"))) {
      var r = read(pair.get(0), pair.get(1), "", true).getFirst().row();
      assertEquals(pair.get(2), HintPreparation.prepare(r).gloss());
      assertFalse(r.curatedGloss());
    }
    var empty = read("bank", "；银行", "", true).getFirst().row();
    assertNull(HintPreparation.prepare(empty).gloss());
  }

  @Test
  void bindsSourceLockDefaultAndEvidenceToNewPreparationDigestWithoutChangingGroundIdentity() {
    assertTrue(
        StardictCsvReader.PREPARATION_POLICY.contains(
            "curated_sha256=37c7973d0361096fc5d6c22c08d836168793d3acfd3fc7404db384232edf1563"));
    assertTrue(
        StardictCsvReader.PREPARATION_POLICY.contains(
            "independent_ground_sha256=337151d8aa58dc9df174c54ee1ca42347e0f9041c0a78740383f23ded451b903"));
    assertFalse(
        StardictCsvReader.PREPARATION_POLICY.contains(
            "curated_sha256=6b5efcba7b09e2e81366c25812f5a2e05f8b0bc1fad220022183ddb7b8fb8a2d"));
  }

  private List<StardictCsvReader.SourceRecord> read(
      String lemma, String translation, String exchange, boolean compact) throws Exception {
    var file = temporary.resolve("source.csv");
    String header =
        compact
            ? "word,translation,oxford,tag,bnc,frq,exchange"
            : "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio";
    var values =
        compact
            ? List.of(lemma, translation, "0", "cet6", "3001", "4001", exchange)
            : List.of(
                lemma, "", "", translation, "", "", "0", "cet6", "3001", "4001", exchange, "", "");
    Files.writeString(
        file,
        header
            + "\n"
            + values.stream()
                .map(v -> "\"" + v.replace("\"", "\"\"") + "\"")
                .collect(java.util.stream.Collectors.joining(","))
            + "\n");
    var records = new ArrayList<StardictCsvReader.SourceRecord>();
    new StardictCsvReader().read(file, records::add);
    return records;
  }
}
