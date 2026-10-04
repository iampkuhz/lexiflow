package io.lexiflow.integration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.sql.DriverManager;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

/**
 * Exercises publication, serving, cache, rollback, and incremental contracts over real processes.
 */
class PublishedPipelineRuntimeTest {
  @Test
  void publishesCanonicalAndStarDictAndExercisesServingReplacementRollbackAndIncrementalRanges()
      throws Exception {
    try (var fixture = new PipelineRuntimeFixture()) {
      fixture.publish(PipelineSyntheticSources.stardict(fixture.temp()));
      long firstVersion = fixture.publishedVersion();
      assertTrue(firstVersion > 9_000_000_000L);
      fixture.startApi();

      assertHint(
          fixture.postText("star", "stardictword"), "star", "stardictword", "合成词", firstVersion);
      assertHint(
          fixture.postText("star-version-one", "lexiflow"),
          "star-version-one",
          "lexiflow",
          "词库v1",
          firstVersion);
      assertNoHints(fixture.postText("star-bad-first", "badstardict"));
      assertEquals(
          "false",
          scalar(
              fixture.jdbcUrl(),
              "SELECT ranked_word::text FROM lexicon_entry WHERE lemma='stardictword'"));
      assertEquals(
          "true",
          scalar(
              fixture.jdbcUrl(),
              "SELECT (gloss IS NULL)::text FROM lexicon_entry WHERE lemma='badstardict'"));

      fixture.publish(PipelineSyntheticSources.canonical(fixture.temp(), "v2"));
      long secondVersion = fixture.publishedVersion();
      assertTrue(secondVersion > firstVersion);
      assertNoHints(fixture.postText("replaced-star", "stardictword"));
      assertHint(
          fixture.postText("canonical-version-two", "lexiflow"),
          "canonical-version-two",
          "lexiflow",
          "词库v2",
          secondVersion);
      assertEquals(200, fixture.get("/actuator/health/readiness").statusCode());

      assertHint(
          fixture.postText("base", "dependable"), "base", "dependable", "可靠的", secondVersion);
      assertHint(fixture.postText("alias", "reliable"), "alias", "reliable", "可靠的", secondVersion);
      assertHint(
          fixture.postText("forms", "dependables"), "forms", "dependables", "可靠的", secondVersion);
      assertNoHints(fixture.postText("basic", "the"));
      assertNoHints(fixture.postText("basic-inflection", "was"));
      assertNoHints(fixture.postText("basic-alias", "basicform"));
      assertHint(
          fixture.postText("focus", "focusword"), "focus", "focusword", "重点词", secondVersion);
      assertHint(fixture.postText("tail", "tailword"), "tail", "tailword", "长尾词", secondVersion);
      assertHint(
          fixture.postText("zero", "zerofrequency"),
          "zero",
          "zerofrequency",
          "零频率词",
          secondVersion);
      assertHint(
          fixture.postText("phrase", "stable phrase"),
          "phrase",
          "stable phrase",
          "可靠短语",
          secondVersion);
      assertNoHints(fixture.postText("noise", "the and"));
      assertNoHints(fixture.postText("bad-gloss", "badgloss"));
      assertEquals(
          "true",
          scalar(
              fixture.jdbcUrl(),
              "SELECT (gloss IS NULL)::text FROM lexicon_entry WHERE lemma='badgloss'"));

      var ambiguous =
          fixture.postTracked(PipelineSyntheticSources.caption("ambiguous", "abdus", true));
      assertNoHints(ambiguous.body());
      assertTrue(number(fixture.awaitRequestEvent(ambiguous.requestId()), "ambiguous") > 0);

      // A published, zero-priority long-tail entry must be fetched before becoming a positive hit.
      var positiveCold =
          fixture.postTracked(PipelineSyntheticSources.caption("positive-cold", "coldprobe", true));
      assertHint(positiveCold.body(), "positive-cold", "coldprobe", "冷键验证", secondVersion);
      var positiveColdEvent = fixture.awaitRequestEvent(positiveCold.requestId());
      assertTrue(number(positiveColdEvent, "cache_misses") > 0);
      assertTrue(number(positiveColdEvent, "db_batches") > 0);
      assertEquals(secondVersion, number(positiveColdEvent, "lexicon_version"));
      var positiveHot =
          fixture.postTracked(PipelineSyntheticSources.caption("positive-hot", "coldprobe", true));
      assertHint(positiveHot.body(), "positive-hot", "coldprobe", "冷键验证", secondVersion);
      var positiveHotEvent = fixture.awaitRequestEvent(positiveHot.requestId());
      assertTrue(number(positiveHotEvent, "positive_hits") > 0);
      assertEquals(0, number(positiveHotEvent, "cache_misses"));
      assertEquals(0, number(positiveHotEvent, "db_batches"));
      assertTrue(number(positiveHotEvent, "version_reads") > 0);
      assertEquals(secondVersion, number(positiveHotEvent, "lexicon_version"));

      // Unknown negative key is absent from the published source and therefore cannot be prewarmed.
      var cold =
          fixture.postTracked(
              PipelineSyntheticSources.caption("cold", "neverpublishedtoken", true));
      assertNoHints(cold.body());
      var coldEvent = fixture.awaitRequestEvent(cold.requestId());
      assertTrue(
          number(coldEvent, "cache_misses") > 0, "cold request was not a cache miss: " + coldEvent);
      assertTrue(
          number(coldEvent, "db_batches") > 0, "cold request did not query DB: " + coldEvent);
      assertEquals(secondVersion, number(coldEvent, "lexicon_version"));
      var hot =
          fixture.postTracked(PipelineSyntheticSources.caption("hot", "neverpublishedtoken", true));
      assertNoHints(hot.body());
      var hotEvent = fixture.awaitRequestEvent(hot.requestId());
      assertTrue(
          number(hotEvent, "negative_hits") > 0, "repeat missed negative cache: " + hotEvent);
      assertTrue(
          number(hotEvent, "version_reads") > 0, "cache hit omitted version read: " + hotEvent);
      assertEquals(secondVersion, number(hotEvent, "lexicon_version"));

      assertHint(
          fixture.postText("new-version", "lexiflow"),
          "new-version",
          "lexiflow",
          "词库v2",
          secondVersion);
      fixture.prohibitThirdLemma();
      var beforeFailure = fixture.databaseSnapshot();
      fixture.publishExpectFailure(
          PipelineSyntheticSources.canonical(fixture.temp(), "thirdversion"));
      assertEquals(
          beforeFailure, fixture.databaseSnapshot(), "failed publish changed published tables");
      assertEquals(secondVersion, fixture.publishedVersion());
      assertEquals(
          "1",
          scalar(
              fixture.jdbcUrl(),
              "SELECT count(*)::text FROM lexicon_entry WHERE lemma='lexiflow'"));
      assertHint(
          fixture.postText("after-failure", "lexiflow"),
          "after-failure",
          "lexiflow",
          "词库v2",
          secondVersion);

      var incremental = fixture.postIncremental();
      assertEquals(List.of("new"), strings(incremental.path("processedKeys")));
      assertEquals(1, incremental.path("hints").size());
      var hint = incremental.path("hints").get(0);
      assertEquals("可靠的", hint.path("chineseGloss").stringValue());
      assertEquals("new", hint.path("startKey").stringValue());
      assertEquals("new", hint.path("endKey").stringValue());
      assertEquals(3, hint.path("startOffset").asInt());
      assertEquals(13, hint.path("endOffset").asInt());
      assertEquals(secondVersion, hint.path("lexiconVersion").asLong());
    }
  }

  private static void assertHint(
      JsonNode response, String key, String text, String gloss, long version) {
    var hints = response.path("hints");
    assertTrue(hints.isArray(), "hints must be an array: " + response);
    assertEquals(1, hints.size(), "expected exactly one hint: " + response);
    var hint = hints.get(0);
    assertEquals(gloss, hint.path("chineseGloss").stringValue());
    assertEquals(version, hint.path("lexiconVersion").asLong());
    assertEquals(key, hint.path("startKey").stringValue());
    assertEquals(key, hint.path("endKey").stringValue());
    assertEquals(0, hint.path("startOffset").asInt());
    assertEquals(text.length(), hint.path("endOffset").asInt());
  }

  private static void assertNoHints(JsonNode response) {
    assertTrue(response.path("hints").isArray());
    assertTrue(response.path("hints").isEmpty(), "expected no hint: " + response);
  }

  private static long number(JsonNode event, String key) {
    return key.equals("lexicon_version")
        ? event.path(key).asLong(-1)
        : event.path("counts").path(key).asLong(0);
  }

  private static List<String> strings(JsonNode node) {
    var values = new ArrayList<String>();
    node.forEach(value -> values.add(value.stringValue()));
    return List.copyOf(values);
  }

  private static String scalar(String jdbcUrl, String sql) throws Exception {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement();
        var rs = statement.executeQuery(sql)) {
      assertTrue(rs.next());
      return rs.getString(1);
    }
  }
}
