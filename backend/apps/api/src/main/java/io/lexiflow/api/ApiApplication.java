package io.lexiflow.api;

import io.lexiflow.enrichment.domain.DeterministicHintPolicy;
import io.lexiflow.lexicon.application.CachedLexiconQueryService;
import io.lexiflow.lexicon.application.LexiconRepository;
import io.lexiflow.lexicon.domain.BuiltinLexiconCatalog;
import io.lexiflow.lexicon.domain.LexiconCatalog;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistenceConfiguration;
import io.lexiflow.workflow.application.EnrichCaptionUseCase;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;

/** LexiFlow 同步 API 的唯一组合根。 */
@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)
@Import(PostgresPersistenceConfiguration.class)
public class ApiApplication {
  private static final org.slf4j.Logger LOGGER =
      org.slf4j.LoggerFactory.getLogger(ApiApplication.class);

  /**
   * 启动 API 进程。
   *
   * @param args 含义：交由 Spring Boot 解析的启动参数。取值范围：非空数组，长度为 0 至任意。
   */
  public static void main(String[] args) {
    SpringApplication.run(ApiApplication.class, args);
  }

  /**
   * 装配确定性字幕提示用例；无显式 PostgreSQL 时保留受限内置演示词库。
   *
   * @param repositories 可选的、平台装配的词库 Repository。
   * @return 可由 HTTP 入口调用的应用用例。
   */
  @Bean
  EnrichCaptionUseCase enrichCaptionUseCase(ObjectProvider<LexiconRepository> repositories) {
    return new EnrichCaptionUseCase(
        lexiconCatalog(repositories.getIfAvailable()), new DeterministicHintPolicy());
  }

  private static LexiconCatalog lexiconCatalog(LexiconRepository repository) {
    if (repository == null) {
      LOGGER.warn(
          "runtime lexicon=builtin-demo; only 5 demo terms, not the imported dictionary;"
              + " set SPRING_DATASOURCE_URL or start_api --database-url for normal use");
      return new BuiltinLexiconCatalog();
    }
    var catalog = new CachedLexiconQueryService(repository, 4_000, 2_000);
    var version = repository.publishedVersion();
    LOGGER.info("runtime lexicon=postgres publishedVersion={}", version);
    if (version == 0) {
      LOGGER.warn(
          "runtime lexicon=empty reason=no-published-version; publish a lexicon before use");
    }
    return catalog;
  }
}
