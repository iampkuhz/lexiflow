package io.lexiflow.api.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import org.junit.jupiter.api.Test;

class LexiconRuntimeTest {
  @Test
  void filteredShortWordsKeepConfirmedReadyStateWithoutStorageReads() {
    for (boolean degraded : List.of(false, true)) {
      var repo = new MutableRepository();
      repo.failPrewarm = degraded;
      var events = new ArrayList<String>();
      var runtime =
          new LexiconRuntime(
              "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
      var confirmed = runtime.probe();
      int beforeEvents = events.size();
      int beforeVersions = repo.versionReads;
      int beforeWarmups = repo.prewarmReads;
      for (var word : List.of("ai", "uh", "um", "i", "to", "it")) {
        var result = runtime.lookupForms(List.of(word));
        assertTrue(result.candidates().isEmpty());
        assertTrue(result.publishedVersion().isEmpty());
        assertEquals(
            io.lexiflow.lexicon.domain.model.LexiconLookupResult.Counts.zero(), result.counts());
      }
      assertEquals(beforeVersions, repo.versionReads);
      assertEquals(beforeWarmups, repo.prewarmReads);
      assertEquals(beforeEvents, events.size());
      assertEquals(confirmed, runtime.probe());
      assertEquals(7, runtime.lookupForms(List.of("quasar")).publishedVersion().orElseThrow());
      assertTrue(runtime.probe().ready());
    }
  }

  @Test
  void filteredShortWordRecoveryKeepsActualProbeCounts() {
    var repo = new MutableRepository();
    repo.version = 0;
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    repo.version = 7;
    int beforeVersions = repo.versionReads;
    int beforeWarmups = repo.prewarmReads;
    var result = runtime.lookupForms(List.of("ai"));
    assertTrue(result.candidates().isEmpty());
    assertTrue(result.publishedVersion().isEmpty());
    assertEquals(repo.versionReads - beforeVersions, result.counts().versionReads());
    assertEquals(1, result.counts().versionReads());
    assertEquals(repo.prewarmReads - beforeWarmups, result.counts().prewarmReads());
    assertEquals(2, result.counts().prewarmReads());
    assertEquals(0, result.counts().queryKeys());
    assertEquals(0, result.counts().dbBatches());
    assertEquals(
        new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 7L, true), runtime.probe());
  }

  @Test
  void filteredShortWordsDoNotMakeUnpublishedRuntimeReady() {
    var repo = new MutableRepository();
    repo.version = 0;
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    var failure =
        assertThrows(LexiconNotReadyException.class, () -> runtime.lookupForms(List.of("ai")));
    assertEquals(LexiconRuntime.Reason.NO_PUBLISHED_DATA, failure.reason());
    assertFalse(runtime.probe().ready());
    assertEquals(0L, runtime.probe().version());
  }

  @Test
  void realRuntimeCacheVersionChangeEmitsOneInvalidationEventAfterFirstBinding() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    assertEquals(
        0, events.stream().filter(s -> s.contains("lexicon.cache.version_changed")).count());
    repo.version = 2;
    runtime.probe();
    assertEquals(
        1, events.stream().filter(s -> s.contains("lexicon.cache.version_changed")).count());
    runtime.probe();
    assertEquals(
        1, events.stream().filter(s -> s.contains("lexicon.cache.version_changed")).count());
  }

  @Test
  void defaultMissingRepositoryIsNotReadyAndDemoIsExplicit() {
    var runtime =
        new LexiconRuntime("formal", null, new StructuredEventLogger((level, json) -> {}));
    assertEquals(LexiconRuntime.Reason.DEPENDENCY_UNAVAILABLE, runtime.probe().reason());
    assertThrows(LexiconNotReadyException.class, () -> runtime.lookupForms(List.of("reliable")));
    var demo = new LexiconRuntime("demo", null, new StructuredEventLogger((level, json) -> {}));
    assertEquals(LexiconRuntime.Reason.DEMO_MODE, demo.probe().reason());
    assertFalse(demo.probe().ready());
    assertFalse(demo.lookupForms(List.of("reliable")).candidates().isEmpty());
    assertEquals(
        "invalid",
        new LexiconRuntime("secret-mode", null, new StructuredEventLogger((level, json) -> {}))
            .probe()
            .mode());
  }

  @Test
  void unavailableExceptionKeepsItsReasonAfterRuntimeRecovery() {
    var repo = new MutableRepository();
    repo.version = 0;
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    var failure =
        assertThrows(LexiconNotReadyException.class, () -> runtime.lookupForms(List.of("quasar")));
    repo.version = 7;
    assertTrue(runtime.probe().ready());
    assertEquals(LexiconRuntime.Reason.NO_PUBLISHED_DATA, failure.reason());
    assertEquals(null, failure.getCause());
  }

  @Test
  void startupOnceAndDependencyTransitionsOnlyWithRecovery() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    assertEquals(1, events.stream().filter(s -> s.contains("runtime.start.completed")).count());
    assertTrue(runtime.probe().ready());
    repo.fail = true;
    assertFalse(runtime.probe().ready());
    assertFalse(runtime.probe().ready());
    repo.fail = false;
    assertTrue(runtime.probe().ready());
    assertTrue(runtime.probe().ready());
    assertEquals(2, events.stream().filter(s -> s.contains("runtime.dependency.changed")).count());
  }

  @Test
  void loggerFailureDoesNotChangeState() {
    var runtime =
        new LexiconRuntime(
            "formal",
            new MutableRepository(),
            new StructuredEventLogger(
                (level, json) -> {
                  throw new IllegalStateException("secret");
                }));
    assertTrue(runtime.probe().ready());
  }

  @Test
  void knownEmptyVersionIsNotUnknownAndNotDependencyFailure() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    repo.version = 0;
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    assertEquals(LexiconRuntime.Reason.NO_PUBLISHED_DATA, runtime.probe().reason());
    assertEquals(0L, runtime.probe().version());
    assertTrue(events.getFirst().contains("\"lexicon_version\":0"));
    repo.version = 7;
    assertTrue(runtime.probe().ready());
    assertEquals(0, events.stream().filter(s -> s.contains("runtime.dependency.changed")).count());
  }

  @Test
  void programErrorsAreNotDisguisedAsDependencyFailures() {
    var repo = new MutableRepository();
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    repo.programFault = true;
    assertThrows(IllegalArgumentException.class, runtime::probe);
    repo.programFault = false;
    // 若错误改写了就绪状态，这次查询会额外发起恢复探测。
    var result = runtime.lookupForms(List.of("quasar"));
    assertEquals(1, result.counts().versionReads());
    assertTrue(runtime.probe().ready());
  }

  @Test
  void metadataFaultIsSchemaMismatchWithoutDependencyTransition() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    repo.metadataFault = true;
    assertEquals(LexiconRuntime.Reason.SCHEMA_MISMATCH, runtime.probe().reason());
    assertEquals(null, runtime.probe().version());
    assertEquals(0, events.stream().filter(s -> s.contains("runtime.dependency.changed")).count());
  }

  @Test
  void prewarmFailureIsDegradedOnlyWhilePublishedReadWorks() {
    var repo = new MutableRepository();
    repo.failPrewarm = true;
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    assertTrue(runtime.probe().ready());
    assertEquals(LexiconRuntime.Reason.PREWARM_DEGRADED, runtime.probe().reason());
    repo.fail = true;
    assertFalse(runtime.probe().ready());
    assertEquals(null, runtime.probe().version());
    repo.fail = false;
    assertTrue(runtime.probe().ready());
    assertEquals(LexiconRuntime.Reason.PREWARM_DEGRADED, runtime.probe().reason());
  }

  @Test
  void recoveringRequestCountsItsProbeAndWarmupWithoutChargingLaterRequests() {
    for (boolean failedAtStartup : List.of(false, true)) {
      var repo = new MutableRepository();
      repo.fail = failedAtStartup;
      if (!failedAtStartup) repo.version = 0;
      var runtime =
          new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
      repo.fail = false;
      repo.version = 7;
      repo.failPrewarm = true;
      int beforeVersions = repo.versionReads;
      int beforeWarmups = repo.prewarmReads;
      var recovered = runtime.lookupForms(List.of("quasar"));
      assertEquals(repo.versionReads - beforeVersions, recovered.counts().versionReads());
      assertEquals(repo.prewarmReads - beforeWarmups, recovered.counts().prewarmReads());
      assertEquals(3, recovered.counts().versionReads());
      assertEquals(2, recovered.counts().prewarmReads());
      beforeVersions = repo.versionReads;
      var cached = runtime.lookupForms(List.of("quasar"));
      assertEquals(repo.versionReads - beforeVersions, cached.counts().versionReads());
      assertEquals(1, cached.counts().versionReads());
      assertEquals(0, cached.counts().prewarmReads());
      assertTrue(runtime.probe().ready());
    }
  }

  @Test
  void failedStartupOmitsUnmeasuredVersionAndWarmupCounts() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    repo.fail = true;
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    assertEquals(1, events.size());
    assertTrue(events.getFirst().contains("DEPENDENCY_UNAVAILABLE"));
    assertFalse(events.getFirst().contains("\"lexicon_version\""));
    assertFalse(events.getFirst().contains("\"counts\""));
    var demo = new LexiconRuntime("demo", repo, new StructuredEventLogger((level, json) -> {}));
    assertEquals(1, repo.versionReads);
    assertFalse(
        new LexiconHealthIndicator(demo)
            .health()
            .getStatus()
            .equals(org.springframework.boot.health.contributor.Status.UP));
    repo.fail = false;
    assertEquals(
        org.springframework.boot.health.contributor.Status.UP,
        new LexiconHealthIndicator(runtime).health().getStatus());
  }

  @Test
  void publicationChangingDuringDegradedWarmupCannotBecomeReady() {
    var repo = new MutableRepository();
    repo.failPrewarm = true;
    repo.clearPublicationDuringPrewarm = true;
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    assertFalse(runtime.probe().ready());
    assertEquals(LexiconRuntime.Reason.NO_PUBLISHED_DATA, runtime.probe().reason());
    assertEquals(0L, runtime.probe().version());
    assertThrows(LexiconNotReadyException.class, () -> runtime.lookupForms(List.of("quasar")));
  }

  private static final class MutableRepository implements LexiconReadRepository {
    boolean fail;
    boolean programFault;
    boolean failPrewarm;
    boolean metadataFault;
    boolean clearPublicationDuringPrewarm;
    long version = 7;
    int versionReads;
    int prewarmReads;

    @Override
    public long publishedVersion() {
      versionReads++;
      if (programFault) throw new IllegalArgumentException("program fault");
      if (metadataFault)
        throw new io.lexiflow.lexicon.application.port.InvalidPublishedLexiconException();
      if (fail) throw new org.springframework.dao.DataAccessResourceFailureException("secret-JDBC");
      return version;
    }

    @Override
    public List<LexiconHintCandidate> findByForms(long version, Collection<String> forms) {
      return List.of();
    }

    @Override
    public List<LexiconHintCandidate> findPrewarmForms(
        long version, LexiconHintAction action, int limit) {
      prewarmReads++;
      if (clearPublicationDuringPrewarm) this.version = 0;
      if (failPrewarm && action == LexiconHintAction.HINT)
        throw new org.springframework.dao.DataAccessResourceFailureException("prewarm connection");
      return List.of();
    }
  }
}
