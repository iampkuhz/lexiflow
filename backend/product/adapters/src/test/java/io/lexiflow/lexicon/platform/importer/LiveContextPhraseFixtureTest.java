package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import java.util.ArrayList;
import java.util.List;
import java.util.OptionalLong;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 待审资料的合成 fixture，不声称已发布、独立审核或真实观看成功。 */
class LiveContextPhraseFixtureTest {
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  @Test
  void exactCandidatesAndExplicitPluralsHaveIndependentlySpecifiedMeanings() {
    var expected = List.of(List.of("nerve fiber", "神经纤维"), List.of("fatty substance", "脂肪性物质"));
    for (var pair : expected) {
      var row =
          ReviewedPhraseCatalog.rows().stream()
              .filter(r -> r.lemma().equals(pair.getFirst()))
              .findFirst()
              .orElseThrow();
      assertEquals(pair.getLast(), row.chineseGloss());
      assertEquals(List.of(pair.getFirst() + "s"), row.inflections());
      for (var form : List.of(row.lemma(), row.inflections().getFirst())) {
        var phrase = phrase(form, row.lemma(), row.chineseGloss());
        assertEquals(
            List.of(pair.getLast()), glosses("Sample " + form + " here.", List.of(phrase)));
        assertEquals(List.of(), glosses(form + "x", List.of(phrase)));
        assertEquals(List.of(), glosses(form.replace(" ", ", "), List.of(phrase)));
        assertEquals(List.of(), glosses(form.replace(" ", " extra "), List.of(phrase)));
      }
    }
  }

  @Test
  void completePhraseCoversItsWordButRejectedOrMissingPhraseLeavesTheOldWord() {
    for (var pair :
        List.of(
            List.of("nerve fibers", "神经纤维", "nerve", "精神"),
            List.of("fatty substances", "脂肪性物质", "fatty", "肥胖的"))) {
      String form = pair.get(0), word = pair.get(2), oldGloss = pair.get(3);
      var component =
          candidate(1, word, word, oldGloss, 900, LexiconEntryKind.WORD, LexiconHintAction.HINT);
      var phrase = phrase(form, form.substring(0, form.length() - 1), pair.get(1));
      assertEquals(List.of(pair.get(1)), glosses(form, List.of(component, phrase)));
      assertEquals(List.of(oldGloss), glosses(word, List.of(component, phrase)));
      assertEquals(List.of(oldGloss), glosses(form, List.of(component)));
      var block =
          candidate(
              3,
              form,
              phrase.normalizedForm(),
              null,
              0,
              LexiconEntryKind.PHRASE,
              LexiconHintAction.BLOCK);
      var other =
          candidate(
              4,
              form,
              phrase.normalizedForm(),
              "冲突短释",
              0,
              LexiconEntryKind.PHRASE,
              LexiconHintAction.HINT);
      assertEquals(List.of(oldGloss), glosses(form, List.of(component, phrase, block)));
      assertEquals(List.of(oldGloss), glosses(form, List.of(component, phrase, other)));
      var reversed = new ArrayList<>(List.of(component, phrase, other));
      java.util.Collections.reverse(reversed);
      assertEquals(List.of(oldGloss), glosses(form, reversed));
    }
  }

  @Test
  void incrementalSameLineCompletesButNeverCrossesLinesGroupsOrReemitsOldOnlyPhrase() {
    for (var pair :
        List.of(List.of("nerve ", "fibers", "神经纤维"), List.of("fatty ", "substance", "脂肪性物质"))) {
      var phrase = phrase(pair.get(0) + pair.get(1), pair.get(0) + pair.get(1), pair.get(2));
      assertEquals(
          List.of(pair.get(2)),
          incremental(
              phrase,
              List.of(
                  group(
                      segment("old", pair.get(0), false, 0),
                      segment("new", pair.get(1), true, 0)))));
      assertEquals(
          List.of(),
          incremental(
              phrase,
              List.of(
                  group(
                      segment("old", pair.get(0), false, 0),
                      segment("new", pair.get(1), true, 1)))));
      assertEquals(
          List.of(),
          incremental(
              phrase,
              List.of(
                  group(segment("old", pair.get(0), false, 0)),
                  group(segment("new", pair.get(1), true, 0)))));
      assertEquals(
          List.of(),
          incremental(
              phrase,
              List.of(
                  group(
                      segment("old", pair.get(0) + pair.get(1) + " ", false, 0),
                      segment("new", "next", true, 0)))));
    }
  }

  private List<String> incremental(
      LexiconHintCandidate phrase, List<CaptionIncrementalRequest.Group> groups) {
    var useCase =
        new EnrichCaptionUseCase(
            forms ->
                new LexiconLookupResult(
                    forms.contains(phrase.normalizedForm()) ? List.of(phrase) : List.of(),
                    OptionalLong.of(1),
                    LexiconLookupResult.Counts.zero()),
            policy);
    return useCase
        .enrichIncrementalMeasured(
            new CaptionIncrementalRequest(
                "fixture-only", null, null, new CaptionIncrementalRequest.Snapshot(groups)))
        .result()
        .hints()
        .stream()
        .map(h -> h.chineseGloss())
        .toList();
  }

  private List<String> glosses(String text, List<LexiconHintCandidate> candidates) {
    return policy.evaluateSelection(text, 0, text.length(), 0, candidates).hints().stream()
        .map(h -> h.chineseGloss())
        .toList();
  }

  private static LexiconHintCandidate phrase(String form, String lemma, String gloss) {
    return candidate(2, form, lemma, gloss, 0, LexiconEntryKind.PHRASE, LexiconHintAction.HINT);
  }

  private static LexiconHintCandidate candidate(
      long id,
      String form,
      String lemma,
      String gloss,
      int priority,
      LexiconEntryKind kind,
      LexiconHintAction action) {
    return new LexiconHintCandidate(
        id,
        action == LexiconHintAction.HINT ? new UUID(0, id) : null,
        1,
        form,
        action,
        gloss,
        priority,
        null,
        null);
  }

  private static CaptionIncrementalRequest.Group group(
      CaptionIncrementalRequest.Segment... segments) {
    return new CaptionIncrementalRequest.Group(null, null, List.of(segments));
  }

  private static CaptionIncrementalRequest.Segment segment(
      String key, String text, boolean append, long line) {
    return new CaptionIncrementalRequest.Segment(key, text, null, append, line);
  }
}
