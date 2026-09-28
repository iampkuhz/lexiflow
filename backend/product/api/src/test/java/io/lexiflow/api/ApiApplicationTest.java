package io.lexiflow.api;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
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
  void readOnlyRepositoryFeedsRealEnrichmentUseCaseWithoutAggregateOrDemoFallback() {
    readOnlyContext.run(
        context -> {
          var useCase = context.getBean(EnrichCaptionUseCase.class);
          assertEquals(0, context.getBeansOfType(LexiconRepository.class).size());
          assertEquals(0, context.getBeansOfType(LexiconPublicationRepository.class).size());
          var result =
              useCase.enrich(
                  new CaptionContext(
                      UUID.randomUUID(), 1, "0".repeat(64), "quasar", 0, "quasar".length()));

          assertEquals("合成词条", result.hints().getFirst().chineseGloss());
        });
  }

  private static LexiconReadRepository readOnlyRepository() {
    var candidate =
        new LexiconHintCandidate(
            UUID.fromString("00000000-0000-0000-0000-000000000001"),
            UUID.fromString("00000000-0000-0000-0000-000000000002"),
            7,
            "en",
            "quasar",
            "quasar",
            LexiconEntryKind.WORD,
            LexiconHintAction.HINT,
            "合成词条",
            100,
            5.0,
            1);
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
