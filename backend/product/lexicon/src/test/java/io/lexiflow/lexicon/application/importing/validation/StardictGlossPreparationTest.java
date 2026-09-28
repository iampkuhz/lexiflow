package io.lexiflow.lexicon.application.importing.validation;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class StardictGlossPreparationTest {
  @Test
  void splitsOnlyFiveSeparatorsOutsideNestedPairedBrackets() {
    assertEquals(
        List.of("首义", "二义", "三义", "四义", "五义", "六义"),
        StardictGlossPreparation.candidates("首义；二义;三义，四义,五义、六义"));
    assertEquals(
        List.of("（甲；乙）", "丙", "丁", "戊【己；庚】"),
        StardictGlossPreparation.candidates("（甲；乙）,丙,丁；戊【己；庚】"));
    assertEquals(List.of("河岸:银行 / 金融"), StardictGlossPreparation.candidates("河岸:银行 / 金融"));
  }

  @Test
  void preservesEmptyFirstCandidateAndCleansOnlyOneDomainPrefix() {
    assertEquals("", StardictGlossPreparation.firstCandidate("；银行"));
    assertEquals(List.of("", "银行"), StardictGlossPreparation.candidates(";银行"));
    assertEquals("银行", StardictGlossPreparation.firstCandidate("[金融] 银行；河岸"));
    assertEquals("[英国] 马特菲尔德", StardictGlossPreparation.firstCandidate("[地名] [英国] 马特菲尔德"));
    assertEquals("（银行；河岸）", StardictGlossPreparation.firstCandidate("（银行；河岸）；其他"));
  }

  @Test
  void invalidBracketsFailCandidatesAndNeverRepairFirstCandidate() {
    for (String value : List.of("[金融）银行；河岸", "银行)；河岸", "银行；（河岸", "银行【错]")) {
      assertThrows(
          IllegalArgumentException.class, () -> StardictGlossPreparation.candidates(value));
      assertEquals("", StardictGlossPreparation.firstCandidate(value));
    }
  }

  @Test
  void safeGlossUsesCodePointLimitAndHanLetterDigitContract() {
    assertTrue(StardictGlossPreparation.safeGloss("𠮷" + "字".repeat(23)));
    assertFalse(StardictGlossPreparation.safeGloss("𠮷" + "字".repeat(24)));
    assertTrue(StardictGlossPreparation.safeGloss("汉A9"));
    for (String value : new String[] {null, "", "abc", "汉 字", "汉，字", "汉\u200b字", "汉\n字"}) {
      assertFalse(StardictGlossPreparation.safeGloss(value));
    }
    assertEquals("银行\u2003", StardictGlossPreparation.firstCandidate("银行\u2003；河岸"));
  }
}
