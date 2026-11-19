package io.lexiflow.enrichment.application.caption;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ContextPhraseIncrementalTest {
  private final LexiconHintCandidate phrase =
      new LexiconHintCandidate(
          1,
          new UUID(0, 1),
          1,
          "en",
          "neural tissue",
          "neural tissue",
          LexiconEntryKind.PHRASE,
          LexiconHintAction.HINT,
          "神经组织",
          0);

  @Test
  void sameLineAppendCompletesPhraseButOldOnlyPhraseIsNeverReemitted() {
    assertEquals(
        List.of("神经组织"),
        hints(
            List.of(
                group(segment("old", "neural ", false, 0), segment("new", "tissue", true, 0)))));
    assertEquals(
        List.of(),
        hints(
            List.of(
                group(
                    segment("old", "neural tissue ", false, 0),
                    segment("new", "changed", true, 0)))));
  }

  @Test
  void doesNotJoinAcrossLinesOrGroupsOrMissingMiddleText() {
    assertEquals(
        List.of(),
        hints(
            List.of(
                group(segment("old", "neural ", false, 0), segment("new", "tissue", true, 1)))));
    assertEquals(
        List.of(),
        hints(
            List.of(
                group(segment("old", "neural ", false, 0)),
                group(segment("new", "tissue", true, 0)))));
    assertEquals(
        List.of(),
        hints(
            List.of(
                group(
                    segment("old", "neural sample ", false, 0),
                    segment("new", "tissue", true, 0)))));
  }

  private List<String> hints(List<CaptionIncrementalRequest.Group> groups) {
    var request =
        new CaptionIncrementalRequest(
            "fixture-topic", null, null, new CaptionIncrementalRequest.Snapshot(groups));
    return new IncrementalCaptionPlan()
        .plan(request).stream()
            .flatMap(
                interval ->
                    new DeterministicHintPolicy()
                            .evaluateSelection(
                                interval.text(),
                                interval.contextStart(),
                                interval.appendEndOffset(),
                                interval.appendStart(),
                                List.of(phrase))
                            .hints()
                            .stream())
            .map(h -> h.chineseGloss())
            .toList();
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
