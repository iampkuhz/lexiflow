package io.lexiflow.enrichment.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.domain.LexiconAlias;
import io.lexiflow.lexicon.domain.LexiconEntry;
import io.lexiflow.lexicon.domain.LexiconEntryKind;
import io.lexiflow.lexicon.domain.LexiconInflection;
import io.lexiflow.lexicon.domain.LexiconProvenance;
import io.lexiflow.lexicon.domain.LexiconSense;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class DeterministicHintPolicyTest {
  private static final String DIGEST = "a".repeat(64);
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  private static LexiconEntry marked(
      LexiconEntry entry, io.lexiflow.lexicon.domain.LexiconHintEligibility status) {
    return new LexiconEntry(
        entry.entryId(),
        entry.lexiconVersion(),
        entry.languageTag(),
        entry.entryKind(),
        entry.lemma(),
        entry.senses(),
        entry.aliases(),
        entry.inflections(),
        entry.provenance(),
        entry.priority(),
        status);
  }

  @Test
  void refusesUnprocessedMaterialsAndRetainsAmbiguityFromExcludedCandidates() {
    assertEquals(
        HintState.NO_PENDING,
        evaluate(
                "hexagon",
                marked(
                    entry("hexagon", "六边形"),
                    io.lexiflow.lexicon.domain.LexiconHintEligibility.UNPROCESSED))
            .state());
    var basic =
        marked(
            entry("ring", "戒指", List.of("circle"), List.of()),
            io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY);
    assertEquals(
        HintState.NO_PENDING, evaluate("circle", List.of(basic, entry("circle", "圆圈"))).state());
  }

  @Test
  void consumesPreparedGlossWithoutDoingRuntimeDeduplication() {
    assertEquals(
        HintState.NO_PENDING, evaluate("hexagon", entry("hexagon", "六边形；[数] 六边形")).state());
    var material = entry("hexagon", "六边形");
    var result = evaluate("A hexagon", material);
    assertEquals(HintState.READY, result.state());
    assertEquals(
        material.senses().getFirst().senseId().toString(), result.hints().getFirst().senseId());
  }

  @Test
  void duplicateExpressionNormalizationNeverChoosesFirstMeaningOrDropsInvalidItems() {
    for (var gloss :
        List.of("六边形；六角形", "六边形；", ";六边形", "六边形；[数]", "六边形；[数] 六边形,六角形", "六边形；[<b>] 六边形")) {
      assertEquals(HintState.NO_PENDING, evaluate("hexagon", entry("hexagon", gloss)).state());
    }
    assertEquals(
        HintState.NO_PENDING,
        evaluate("hexagon", entry("hexagon", List.of("六边形", "六边形；六边形"))).state());
  }

  @Test
  void acceptsOnlyOneShortChineseSenseWithoutUnsafeCharacters() {
    assertEquals(HintState.READY, evaluate("reliable", entry("reliable", "可靠的")).state());

    for (var unsafe :
        List.of(
            "可靠的；可信赖", "reliable", "一".repeat(25), "可靠\u0000", "可靠\u200B", "可靠（结果）", "<b>可靠</b>")) {
      assertEquals(HintState.NO_PENDING, evaluate("reliable", entry("reliable", unsafe)).state());
    }

    assertEquals(
        HintState.NO_PENDING,
        evaluate("reliable", entry("reliable", List.of("可靠的", "可信赖"))).state());
  }

  @Test
  void suppressesFunctionWordsEvenWhenDictionaryHasOneShortGloss() {
    for (var word :
        List.of(
            "the", "THE", "a", "an", "she", "is", "could", "of", "and", "that", "very", "also",
            "beside", "often")) {
      assertEquals(
          HintState.NO_PENDING,
          evaluate(
                  word,
                  marked(
                      entry(word.toLowerCase(java.util.Locale.ROOT), "那"),
                      io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY))
              .state());
    }
    var text = "The reliable method";
    assertEquals(
        List.of("reliable"),
        surfaces(
            text,
            evaluate(
                text,
                List.of(
                    marked(
                        entry("the", "那"),
                        io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY),
                    entry("reliable", "可靠的")))));
  }

  @Test
  void functionWordAliasesAndInflectionsCannotBypassHintEligibility() {
    assertEquals(
        HintState.NO_PENDING,
        evaluate(
                "the",
                marked(
                    entry("demonstrative", "那", List.of("the"), List.of()),
                    io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY))
            .state());
    assertEquals(
        HintState.NO_PENDING,
        evaluate(
                "was",
                marked(
                    entry("exist", "存在", List.of(), List.of("was")),
                    io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY))
            .state());
  }

  @Test
  void functionWordsInsideReliablePhrasesDoNotSuppressWholePhrase() {
    var text = "on the contrary";
    var phrase = entry(text, "相反");
    assertEquals(List.of(text), surfaces(text, evaluate(text, List.of(entry("the", "那"), phrase))));
    assertEquals(HintState.READY, evaluate("theory", entry("theory", "理论")).state());
  }

  @Test
  void returnsTheOnlyEligibleSenseIdentityInsteadOfAClientDerivedValue() {
    var entry = entry("reliable", "可靠的");
    var result = evaluate("reliable", entry);

    assertEquals(sense("可靠的").senseId().toString(), result.hints().getFirst().senseId());
  }

  @Test
  void rejectsTheEntireCandidateBatchWhenVersionsDiffer() {
    var versionOne = entry("reliable", "可靠的", 1);
    var versionTwo = entry("context", "语境", 2);

    assertEquals(
        HintState.NO_PENDING, evaluate("reliable", List.of(versionOne, versionTwo)).state());
  }

  @Test
  void rejectsAnAmbiguousSurfaceProvidedByAliasAndInflection() {
    var aliasEntry = entry("dependable", "可靠的", List.of("reliable"), List.of());
    var inflectionEntry = entry("rely", "依赖", List.of(), List.of("reliable"));

    assertEquals(
        HintState.NO_PENDING, evaluate("reliable", List.of(aliasEntry, inflectionEntry)).state());
  }

  @Test
  void selectsLongestNonOverlappingRangesOncePerEntryAndLimitsResults() {
    var reliable = entry("reliable", "可靠的");
    var phrase = entry("very reliable", "非常可靠");
    var context = entry("context", "语境");
    var caption = entry("caption", "字幕");
    var text = "reliable very reliable context caption reliable";

    var result = evaluate(text, List.of(caption, context, reliable, phrase));

    assertEquals(HintState.READY, result.state());
    assertEquals(List.of("reliable", "very reliable", "context"), surfaces(text, result));
  }

  @Test
  void prefersPhraseOverContainedWordAndKeepsCandidateOrderIrrelevant() {
    var reliable = entry("reliable", "可靠的");
    var phrase = entry("very reliable", "非常可靠");
    var context = entry("context", "语境");
    var text = "very reliable in context";

    var forward = evaluate(text, List.of(reliable, phrase, context));
    var reversed = evaluate(text, List.of(context, phrase, reliable));

    assertEquals(List.of("very reliable", "context"), surfaces(text, forward));
    assertEquals(forward.hints(), reversed.hints());
  }

  @Test
  void matchesAliasesAndInflectionsAtTheirActualSurfaceOffsets() {
    var entry = entry("reliable", "可靠的", List.of("dependable"), List.of("reliably"));

    for (var surface : List.of("dependable", "reliably")) {
      var text = "It works " + surface + ".";
      var result = evaluate(text, entry);

      assertEquals(HintState.READY, result.state());
      assertEquals(surface, surfaces(text, result).getFirst());
    }
  }

  @Test
  void doesNotMatchWordsInsideOtherWordsOrAcrossTheTargetRange() {
    var result = evaluate("unreliable reliable_test reliable", entry("reliable", "可靠的"));

    assertEquals(List.of("reliable"), surfaces("unreliable reliable_test reliable", result));
    assertEquals(
        HintState.NO_PENDING,
        policy.evaluate(context("reliable", 0, 4), List.of(entry("reliable", "可靠的"))).state());
  }

  @Test
  void preservesUtf16OffsetsAndRejectsTargetRangesThatSplitSurrogatePairs() {
    var text = "🤖 reliable";
    var result = evaluate(text, entry("reliable", "可靠的"));
    var hint = result.hints().getFirst();

    assertEquals(3, hint.startOffset());
    assertEquals(11, hint.endOffset());
    assertEquals("reliable", text.substring(hint.startOffset(), hint.endOffset()));
    assertThrows(
        IllegalArgumentException.class,
        () -> new CaptionContext(UUID.randomUUID(), 1, DIGEST, text, 1, text.length()));
  }

  private CaptionHintResult evaluate(String text, LexiconEntry entry) {
    return evaluate(text, List.of(entry));
  }

  private CaptionHintResult evaluate(String text, List<LexiconEntry> entries) {
    return policy.evaluate(context(text, 0, text.length()), entries);
  }

  private static CaptionContext context(String text, int startOffset, int endOffset) {
    return new CaptionContext(UUID.randomUUID(), 1, DIGEST, text, startOffset, endOffset);
  }

  private static List<String> surfaces(String text, CaptionHintResult result) {
    return result.hints().stream()
        .map(hint -> text.substring(hint.startOffset(), hint.endOffset()))
        .toList();
  }

  private static LexiconEntry entry(String term, String gloss) {
    return entry(term, gloss, 1);
  }

  private static LexiconEntry entry(String term, String gloss, long lexiconVersion) {
    return entry(term, lexiconVersion, List.of(gloss), List.of(), List.of());
  }

  private static LexiconEntry entry(String term, List<String> glosses) {
    return entry(term, 1, glosses, List.of(), List.of());
  }

  private static LexiconEntry entry(
      String term, String gloss, List<String> aliases, List<String> inflections) {
    return entry(term, 1, List.of(gloss), aliases, inflections);
  }

  private static LexiconEntry entry(
      String term,
      long lexiconVersion,
      List<String> glosses,
      List<String> aliases,
      List<String> inflections) {
    return new LexiconEntry(
        UUID.nameUUIDFromBytes(term.getBytes(StandardCharsets.UTF_8)),
        lexiconVersion,
        "en",
        term.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        term,
        glosses.stream().map(DeterministicHintPolicyTest::sense).toList(),
        aliases.stream().map(LexiconAlias::new).toList(),
        inflections.stream().map(LexiconInflection::new).toList(),
        new LexiconProvenance("synthetic", "MIT", DIGEST, Instant.EPOCH));
  }

  private static LexiconSense sense(String gloss) {
    return new LexiconSense(
        UUID.nameUUIDFromBytes(gloss.getBytes(StandardCharsets.UTF_8)),
        gloss,
        "synthetic",
        "synthetic");
  }
}
