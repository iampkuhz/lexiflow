package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 自编通用句，不含实际字幕；完整构式与单词共存而不冒充语境全义消歧。 */
class GeneralConstructionTest {
  private static final List<List<String>> CASES =
      List.of(
          List.of("knob of butter", "一小块黄油", "knob", "把手"),
          List.of("splash of", "少量", "splash", "飞溅"),
          List.of("with ease", "轻松地", "ease", "减轻"),
          List.of("at ease", "自在", "ease", "减轻"),
          List.of("awful lot", "大量", "awful", "可怕的"));
  private final DeterministicHintPolicy policy = new DeterministicHintPolicy();

  @Test
  void completePhrasesWinOnlyContainedWordsAndRetainStableDistinctIdentities() {
    for (var c : CASES) {
      var word = candidate(1, c.get(2), c.get(3), false);
      var phrase = candidate(2, c.get(0), c.get(1), true);
      var other = candidate(3, "sample", "样本", false);
      var candidates = List.of(word, phrase, other);
      var hints = policy.evaluate(c.get(0) + " sample", 0, c.get(0).length() + 7, 0, candidates);
      assertEquals(List.of(c.get(1), "样本"), hints.stream().map(h -> h.chineseGloss()).toList());
      assertEquals("2", hints.getFirst().lexiconEntryId());
      assertNotEquals("1", hints.getFirst().lexiconEntryId());
      assertEquals(
          List.of(c.get(3)),
          policy.evaluate(c.get(2), 0, c.get(2).length(), 0, candidates).stream()
              .map(h -> h.chineseGloss())
              .toList());
      assertEquals(
          List.of(),
          policy.evaluate(
              c.get(0).replace(" ", ", "),
              0,
              c.get(0).replace(" ", ", ").length(),
              0,
              List.of(phrase)));
      assertEquals(
          List.of(), policy.evaluate(c.get(0) + "x", 0, c.get(0).length() + 1, 0, List.of(phrase)));
      assertEquals(
          hints, policy.evaluate(c.get(0) + " sample", 0, c.get(0).length() + 7, 0, candidates));
      var repeated = c.get(0) + " then " + c.get(0);
      assertEquals(2, policy.evaluate(repeated, 0, repeated.length(), 0, List.of(phrase)).size());
    }
  }

  @Test
  void eachFinalWordArrivalCompletesWithinThreeWordWindowWithoutReemittingOldOnlyPhrase() {
    for (var c : CASES) {
      var phrase = candidate(2, c.get(0), c.get(1), true);
      int split = c.get(0).lastIndexOf(' ') + 1;
      var prefix = c.get(0).substring(0, split);
      var suffix = c.get(0).substring(split);
      assertEquals(List.of(), policy.evaluate(prefix, 0, prefix.length(), 0, List.of(phrase)));
      var request = request(segment("old", prefix, false, 0), segment("new", suffix, true, 0));
      var plan = new IncrementalCaptionPlan().plan(request);
      assertEquals(1, plan.size());
      var interval = plan.getFirst();
      var annotations =
          policy.evaluate(
              interval.text(),
              interval.contextStart(),
              interval.appendEndOffset(),
              interval.appendStart(),
              List.of(phrase));
      var hints = new IncrementalHintMapper().map(request, interval, annotations);
      assertEquals(1, hints.size());
      assertEquals("old", hints.getFirst().startKey());
      assertEquals("new", hints.getFirst().endKey());
      assertEquals("2", hints.getFirst().lexiconEntryId());
      assertEquals(c.get(1), hints.getFirst().chineseGloss());
      assertEquals(
          List.of(),
          policy.evaluate(
              c.get(0) + " next", 0, c.get(0).length() + 5, c.get(0).length(), List.of(phrase)));
      for (var separate :
          new IncrementalCaptionPlan()
              .plan(request(segment("old", prefix, false, 0), segment("new", suffix, true, 1)))) {
        assertEquals(
            List.of(),
            policy.evaluate(
                separate.text(),
                separate.contextStart(),
                separate.appendEndOffset(),
                separate.appendStart(),
                List.of(phrase)));
      }
    }
  }

  private static LexiconHintCandidate candidate(
      long id, String form, String gloss, boolean phrase) {
    return new LexiconHintCandidate(
        id,
        new UUID(0, id),
        1,
        form,
        LexiconHintAction.HINT,
        gloss,
        phrase ? 0 : 900);
  }

  private static CaptionIncrementalRequest request(CaptionIncrementalRequest.Segment... segments) {
    return new CaptionIncrementalRequest(
        "fixture-topic",
        null,
        null,
        new CaptionIncrementalRequest.Snapshot(
            List.of(new CaptionIncrementalRequest.Group(null, null, List.of(segments)))));
  }

  private static CaptionIncrementalRequest.Segment segment(
      String key, String text, boolean append, long line) {
    return new CaptionIncrementalRequest.Segment(key, text, null, append, line);
  }
}
