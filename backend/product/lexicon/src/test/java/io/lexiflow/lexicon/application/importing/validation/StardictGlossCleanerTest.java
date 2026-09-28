package io.lexiflow.lexicon.application.importing.validation;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class StardictGlossCleanerTest {
  @Test
  void returnsExistingSafeGlossWithoutApplyingTransformations() {
    var result = StardictGlossCleaner.clean("bank", "银行；河岸");
    assertEquals("银行", result.candidate());
    assertEquals("existing_safe", result.decisiveRule());
    assertTrue(result.matchedRules().isEmpty());
  }

  @Test
  void followsFixedPrefixAndTextRuleOrderWithPerStepValidation() {
    var result = StardictGlossCleaner.clean("mri", "[医] Magnetic Resonance Imaging 磁共振成像");
    assertEquals("磁共振成像", result.candidate());
    assertEquals("acronym_expansion", result.decisiveRule());
    assertEquals(List.of("source_label", "acronym_expansion"), result.matchedRules());

    var prefix = StardictGlossCleaner.clean("john", "(John)人名；(英)约翰");
    assertEquals("约翰", prefix.candidate());
    assertEquals("person_header", prefix.decisiveRule());
  }

  @Test
  void prefixAndAngleRulesAreNarrowAndDoNotLoopOrGuessUnknownLabels() {
    assertEquals("肾病", StardictGlossCleaner.clean("kidney", "【医】肾病").candidate());
    var sourceThenAcronym =
        StardictGlossCleaner.clean("mri", "[医]Magnetic Resonance Imaging 磁共振成像");
    assertEquals("磁共振成像", sourceThenAcronym.candidate());
    assertEquals(List.of("source_label", "acronym_expansion"), sourceThenAcronym.matchedRules());
    assertEquals("肾病", StardictGlossCleaner.clean("kidney", "〔医〕肾病").candidate());
    assertEquals("术语", StardictGlossCleaner.clean("term", "[医、化]术语").candidate());
    assertEquals("小孩", StardictGlossCleaner.clean("child", "<口>小孩").candidate());
    assertEquals("再见", StardictGlossCleaner.clean("term", "＜法＞再见").candidate());
    assertEquals("朋友", StardictGlossCleaner.clean("term", "〈美俚〉朋友").candidate());
    var unknown = StardictGlossCleaner.clean("term", "<专业>术语");
    assertEquals("<专业>术语", unknown.candidate());
    assertEquals("unresolved", unknown.decisiveRule());
    assertEquals("unresolved", StardictGlossCleaner.clean("term", "〈口>小孩").decisiveRule());
    assertEquals("unresolved", StardictGlossCleaner.clean("term", "＜美俚＞朋友").decisiveRule());
    for (String rejected : List.of("肾【功能】病", "[医/化]术语", "[" + "医".repeat(13) + "]术语")) {
      assertEquals(
          "unresolved", StardictGlossCleaner.clean("term", rejected).decisiveRule(), rejected);
    }
    var once = StardictGlossCleaner.clean("term", "【医】[英]肾");
    assertEquals(List.of("source_label"), once.matchedRules());
    assertEquals("[英]肾", once.workingGloss());
  }

  @Test
  void englishExpansionAndPersonHeaderRequireTheirFrozenShapes() {
    assertEquals(
        "磁共振成像",
        StardictGlossCleaner.clean("mri", "[=magnetic resonance imaging]磁共振成像").candidate());
    assertEquals("电子邮件", StardictGlossCleaner.clean("email", "(=electronic mail)电子邮件").candidate());
    assertEquals("unresolved", StardictGlossCleaner.clean("term", "(=中文)释义").decisiveRule());
    assertEquals(
        "unresolved",
        StardictGlossCleaner.clean("term", "[=magnetic resonance imaging释义").decisiveRule());
    assertEquals(
        "unresolved", StardictGlossCleaner.clean("janedoe", "(John)人名；(英)约翰").decisiveRule());
    assertEquals("unresolved", StardictGlossCleaner.clean("jane", "(John)人名；(英)约翰").decisiveRule());
  }

  @Test
  void leadingHanAndSpacingRulesPreserveMeaningCharacters() {
    assertEquals("每年一次发情期的", StardictGlossCleaner.clean("term", "(每年)一次发情期的").candidate());
    assertEquals("心脏病", StardictGlossCleaner.clean("term", "心 脏 病").candidate());
    assertEquals("维生素B12", StardictGlossCleaner.clean("term", "维生素 B12").candidate());
    assertEquals(
        "heart disease", StardictGlossCleaner.clean("term", "heart disease").workingGloss());
    assertEquals("unresolved", StardictGlossCleaner.clean("term", "(不)许可").decisiveRule());
    for (String value : List.of("(甲)释义", "(甲、乙)释义", "(every year)释义")) {
      assertEquals("unresolved", StardictGlossCleaner.clean("term", value).decisiveRule(), value);
    }
    for (String value : List.of("heart\tdisease", "12 34")) {
      assertEquals(value, StardictGlossCleaner.clean("term", value).workingGloss());
    }
    var english = StardictGlossCleaner.clean("term", "alpha beta");
    assertEquals("alpha beta", english.workingGloss());
    assertEquals("unresolved", english.decisiveRule());
  }

  @Test
  void abbreviationRequiresCompleteInitialismMatch() {
    assertEquals(
        "磁共振成像", StardictGlossCleaner.clean("mri", "magnetic resonance imaging 磁共振成像").candidate());
    assertEquals(
        "unresolved",
        StardictGlossCleaner.clean("xyz", "magnetic resonance imaging 磁共振成像").decisiveRule());
    assertEquals(
        "美国古旧书商协会",
        StardictGlossCleaner.clean(
                "abaa", "Antiquarian Booksellers Association of America 美国古旧书商协会")
            .candidate());
    assertEquals(
        "磁共振成像",
        StardictGlossCleaner.clean("mri", "Magnetic and Resonance Imaging 磁共振成像").candidate());
    assertEquals(
        "unresolved",
        StardictGlossCleaner.clean("mri", "Magnetic Unknown Resonance Imaging 磁共振成像")
            .decisiveRule());
  }

  @Test
  void tailNotesProtectRomanNumeralsAndShortTechnicalSymbols() {
    assertEquals("兄弟", StardictGlossCleaner.clean("bro", "兄弟（brother）").candidate());
    for (String value : List.of("氧化铱(III)", "压力(mm)", "兄弟(IV)", "兄弟(AB)")) {
      assertEquals(value, StardictGlossCleaner.clean("term", value).candidate(), value);
    }
    var inlineAlternative = StardictGlossCleaner.clean("term", "不受重视（或被忽视）的人");
    assertEquals("不受重视（或被忽视）的人", inlineAlternative.candidate());
    assertEquals("unresolved", inlineAlternative.decisiveRule());
    assertEquals("刚果", StardictGlossCleaner.clean("term", "刚果(刚(金))").candidate());
    assertEquals("阿尔茨海默氏病", StardictGlossCleaner.clean("term", "阿尔茨海默氏病(旧称)").candidate());
    assertEquals("海牛", StardictGlossCleaner.clean("term", "海牛(一种海生动物)").candidate());
    assertEquals("市名", StardictGlossCleaner.clean("term", "市名(某地（金国）)").candidate());
    for (String value :
        List.of("兄弟()", "市名(说明)(English)", "兄弟(brother)关系", "(brother)名词", "兄弟(brother")) {
      assertEquals("unresolved", StardictGlossCleaner.clean("term", value).decisiveRule(), value);
    }
  }

  @Test
  void medicalInsertRequiresImmutableRawMedicalScope() {
    assertEquals("甲床瘤", StardictGlossCleaner.clean("onychoma", "[医]甲[床]瘤").candidate());
    assertEquals("甲[床]瘤", StardictGlossCleaner.clean("term", "甲[床]瘤").candidate());
    assertEquals("甲[坚硬床]瘤", StardictGlossCleaner.clean("term", "[医]甲[坚硬床]瘤").candidate());
  }

  @Test
  void unresolvedAndSafeResultRetainWorkingSourceAndRuleAttribution() {
    var unresolved = StardictGlossCleaner.clean("term", "中文释义……");
    assertEquals("unresolved", unresolved.decisiveRule());
    assertEquals("中文释义……", unresolved.workingGloss());
    String original = "【地名】新宿";
    var converted = StardictGlossCleaner.clean("term", original);
    assertEquals("新宿", converted.candidate());
    assertEquals("source_label", converted.decisiveRule());
    assertEquals("【地名】新宿", original);
  }
}
