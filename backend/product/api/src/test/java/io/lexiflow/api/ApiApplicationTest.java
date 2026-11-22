package io.lexiflow.api;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

import io.lexiflow.api.captiondebug.CaptionDebugController;
import io.lexiflow.api.runtime.SoftwareIdentity;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class ApiApplicationTest {
  private final ApplicationContextRunner readOnlyContext =
      new ApplicationContextRunner()
          .withUserConfiguration(ApiApplication.class)
          .withBean(LexiconReadRepository.class, ApiApplicationTest::readOnlyRepository);

  @Test
  void contextLoads() {}

  @Test
  void captionDisplayDiagnosticsRequireExplicitLocalOptIn() {
    readOnlyContext.run(
        context -> assertEquals(0, context.getBeansOfType(CaptionDebugController.class).size()));
    readOnlyContext
        .withPropertyValues("lexiflow.caption-debug.enabled=true")
        .run(
            context ->
                assertEquals(1, context.getBeansOfType(CaptionDebugController.class).size()));
  }

  @Test
  void softwareIdentityComesFromEmbeddedBuildResource() throws Exception {
    var identity = new SoftwareIdentity();
    assertNotNull(identity.version());
    // 独立消费任务提供的版本和内嵌 JSON，避免重复读取同一 txt 形成循环断言。
    String expectedVersion = System.getProperty("lexiflow.build.version");
    assertNotNull(expectedVersion, "build task must provide software identity");
    assertEquals(expectedVersion, identity.version());
    try (var resource =
        getClass().getClassLoader().getResourceAsStream("META-INF/lexiflow-build.json")) {
      assertNotNull(resource, "META-INF/lexiflow-build.json must be on test classpath");
      var embedded = new tools.jackson.databind.ObjectMapper().readTree(resource);
      assertEquals(9, embedded.size());
      assertEquals(1, embedded.get("schemaVersion").asInt());
      assertEquals(expectedVersion, embedded.get("softwareVersion").asString());
    }
  }

  @Test
  void readOnlyRepositoryFeedsRealEnrichmentUseCaseWithoutAggregateOrDemoFallback() {
    readOnlyContext.run(
        context -> {
          var useCase = context.getBean(EnrichCaptionUseCase.class);
          assertEquals(0, context.getBeansOfType(LexiconRepository.class).size());
          assertEquals(0, context.getBeansOfType(LexiconPublicationRepository.class).size());
          var result =
              useCase
                  .enrichIncrementalMeasured(
                      new CaptionIncrementalRequest(
                          List.of(),
                          new CaptionIncrementalRequest.Snapshot(
                              List.of(
                                  new CaptionIncrementalRequest.Group(
                                      List.of(
                                          new CaptionIncrementalRequest.Segment(
                                              "a", "quasar", true, 0)))))))
                  .result();

          assertEquals("合成词条", result.hints().getFirst().chineseGloss());
          assertEquals("9007199254740993", result.hints().getFirst().lexiconEntryId());
        });
  }

  private static LexiconReadRepository readOnlyRepository() {
    var candidate =
        new LexiconHintCandidate(
            9007199254740993L,
            UUID.fromString("00000000-0000-0000-0000-000000000002"),
            7,
            "quasar",
            LexiconHintAction.HINT,
            "合成词条",
            100,
            null);
    return new LexiconReadRepository() {
      @Override
      public long publishedVersion() {
        return 7;
      }

      @Override
      public List<LexiconHintCandidate> findByForms(long version, Collection<String> forms) {
        return forms.contains("quasar") ? List.of(candidate) : List.of();
      }

      @Override
      public List<LexiconHintCandidate> findPrewarmForms(
          long version, LexiconHintAction action, int limit) {
        return List.of();
      }
    };
  }
}
