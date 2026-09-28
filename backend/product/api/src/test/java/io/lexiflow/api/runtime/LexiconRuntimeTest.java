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
  void defaultMissingRepositoryIsNotReadyAndDemoIsExplicit() {
    var runtime =
        new LexiconRuntime("formal", null, new StructuredEventLogger((level, json) -> {}));
    assertEquals(LexiconRuntime.Reason.DEPENDENCY_UNAVAILABLE, runtime.state().reason());
    assertThrows(LexiconNotReadyException.class, () -> runtime.lookupForms(List.of("reliable")));
    var demo = new LexiconRuntime("demo", null, new StructuredEventLogger((level, json) -> {}));
    assertEquals(LexiconRuntime.Reason.DEMO_MODE, demo.state().reason());
    assertFalse(demo.state().ready());
    assertFalse(demo.lookupForms(List.of("reliable")).candidates().isEmpty());
    assertEquals(
        "invalid",
        new LexiconRuntime("secret-mode", null, new StructuredEventLogger((level, json) -> {}))
            .state()
            .mode());
  }

  @Test
  void startupOnceAndDependencyTransitionsOnlyWithRecovery() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    assertEquals(1, events.stream().filter(s -> s.contains("runtime.start.completed")).count());
    assertTrue(runtime.state().ready());
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
    assertTrue(runtime.state().ready());
  }

  @Test
  void knownEmptyVersionIsNotUnknownAndNotDependencyFailure() {
    var events = new ArrayList<String>();
    var repo = new MutableRepository();
    repo.version = 0;
    var runtime =
        new LexiconRuntime(
            "formal", repo, new StructuredEventLogger((level, json) -> events.add(json)));
    assertEquals(LexiconRuntime.Reason.NO_PUBLISHED_DATA, runtime.state().reason());
    assertEquals(0L, runtime.state().version());
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
    assertEquals(LexiconRuntime.Reason.OK, runtime.state().reason());
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
    assertEquals(null, runtime.state().version());
    assertEquals(0, events.stream().filter(s -> s.contains("runtime.dependency.changed")).count());
  }

  @Test
  void prewarmFailureIsDegradedOnlyWhilePublishedReadWorks() {
    var repo = new MutableRepository();
    repo.failPrewarm = true;
    var runtime =
        new LexiconRuntime("formal", repo, new StructuredEventLogger((level, json) -> {}));
    assertTrue(runtime.state().ready());
    assertEquals(LexiconRuntime.Reason.PREWARM_DEGRADED, runtime.state().reason());
    repo.fail = true;
    assertFalse(runtime.probe().ready());
    assertEquals(null, runtime.state().version());
    repo.fail = false;
    assertTrue(runtime.probe().ready());
    assertEquals(LexiconRuntime.Reason.PREWARM_DEGRADED, runtime.state().reason());
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
      assertTrue(runtime.state().ready());
      assertEquals(repo.versionReads - beforeVersions, recovered.counts().versionReads());
      assertEquals(repo.prewarmReads - beforeWarmups, recovered.counts().prewarmReads());
      assertEquals(3, recovered.counts().versionReads());
      assertEquals(2, recovered.counts().prewarmReads());
      beforeVersions = repo.versionReads;
      var cached = runtime.lookupForms(List.of("quasar"));
      assertEquals(repo.versionReads - beforeVersions, cached.counts().versionReads());
      assertEquals(1, cached.counts().versionReads());
      assertEquals(0, cached.counts().prewarmReads());
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
    assertFalse(runtime.state().ready());
    assertEquals(1, events.size());
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
    assertFalse(runtime.state().ready());
    assertEquals(LexiconRuntime.Reason.NO_PUBLISHED_DATA, runtime.state().reason());
    assertEquals(0L, runtime.state().version());
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
