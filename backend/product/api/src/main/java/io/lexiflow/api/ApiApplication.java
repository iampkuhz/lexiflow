package io.lexiflow.api;

import io.lexiflow.api.deployment.DeploymentDataCommand;
import io.lexiflow.api.hints.SegmentAnalysisLog;
import io.lexiflow.api.preferences.PreferencesSchemaCommand;
import io.lexiflow.api.release.ReleaseDatasetCommand;
import io.lexiflow.api.runtime.LexiconHealthIndicator;
import io.lexiflow.api.runtime.LexiconRuntime;
import io.lexiflow.api.runtime.SoftwareIdentity;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.enrichment.platform.preferences.SqlitePreferencesStore;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetVerifier;
import io.lexiflow.lexicon.platform.persistence.SqlitePersistenceConfiguration;
import io.lexiflow.observability.platform.FileSegmentAnalysisStore;
import io.lexiflow.observability.platform.SegmentAnalysisStore;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.nio.file.Files;
import java.nio.file.Path;
import javax.sql.DataSource;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;

/** LexiFlow 同步 API 的唯一组合根。 */
@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)
@org.springframework.scheduling.annotation.EnableScheduling
@Import(SqlitePersistenceConfiguration.class)
public class ApiApplication {

  /**
   * 启动 API 进程。
   *
   * @param args 含义：交由 Spring Boot 解析的启动参数。取值范围：非空数组，长度为 0 至任意。
   */
  public static void main(String[] args) throws Exception {
    if (java.util.Arrays.equals(args, new String[] {"--storage-contract"})) {
      io.lexiflow.api.deployment.StorageContract.bundled();
      return;
    }
    if (args.length == 3 && "--verify-storage".equals(args[0])) {
      io.lexiflow.api.deployment.StorageContract.verify(args[1], Path.of(args[2]));
      System.out.println("PASS");
      return;
    }
    if (java.util.Arrays.equals(args, new String[] {"--api-contract"})) {
      System.out.println(io.lexiflow.api.runtime.ContractIdentity.apiContract());
      return;
    }
    if (args.length > 0 && "--init-preferences".equals(args[0])) {
      System.exit(PreferencesSchemaCommand.run(java.util.Arrays.copyOfRange(args, 1, args.length)));
      return;
    }
    if (args.length > 0 && "--release-dataset".equals(args[0])) {
      System.exit(ReleaseDatasetCommand.run(args));
      return;
    }
    if (args.length > 0 && "--deployment-data".equals(args[0])) {
      System.exit(DeploymentDataCommand.run(args));
      return;
    }
    SpringApplication.run(ApiApplication.class, args);
  }

  @Bean
  StructuredEventLogger structuredEventLogger() {
    return new StructuredEventLogger();
  }

  @Bean
  SoftwareIdentity softwareIdentity() {
    return new SoftwareIdentity();
  }

  @Bean
  LexiconRuntime lexiconRuntime(
      ObjectProvider<LexiconReadRepository> repositories,
      @org.springframework.beans.factory.annotation.Qualifier("lexiflowDataSource")
          ObjectProvider<DataSource> dataSources,
      @Value("${lexiflow.runtime.mode:formal}") String mode,
      StructuredEventLogger events)
      throws Exception {
    var dataSource = dataSources.getIfAvailable();
    if (dataSource != null) {
      try (var schema = ApiApplication.class.getResourceAsStream("/META-INF/lexiflow-schema.sql");
          var connection = dataSource.getConnection()) {
        if (schema == null) throw new IllegalStateException("trusted schema is unavailable");
        SqliteDatasetVerifier.verifyStructure(connection, schema.readAllBytes());
      }
    }
    return new LexiconRuntime(mode, repositories.getIfAvailable(), events);
  }

  @Bean("customLexiconHealthIndicator")
  LexiconHealthIndicator customLexiconHealthIndicator(LexiconRuntime runtime) {
    return new LexiconHealthIndicator(runtime);
  }

  @Bean
  EnrichCaptionUseCase enrichCaptionUseCase(LexiconRuntime runtime) {
    return new EnrichCaptionUseCase(runtime, new DeterministicHintPolicy());
  }

  @Bean
  @org.springframework.boot.autoconfigure.condition.ConditionalOnExpression(
      "T(org.springframework.util.StringUtils).hasText('${lexiflow.preferences.jdbc-url:}')")
  LocalPreferences localPreferences(@Value("${lexiflow.preferences.jdbc-url}") String url) {
    PreferencesSchemaCommand.requireCurrent(url);
    var preferences = new LocalPreferences(new SqlitePreferencesStore(url));
    // 未初始化或损坏的卷必须阻止启动；此处绝不创建数据库。
    preferences.read();
    return preferences;
  }

  @Bean
  @org.springframework.boot.autoconfigure.condition.ConditionalOnExpression(
      "T(org.springframework.util.StringUtils).hasText('${lexiflow.preferences.jdbc-url:}')")
  io.lexiflow.enrichment.application.preferences.RecentHintFrequency recentHintFrequency(
      @Value("${lexiflow.preferences.jdbc-url}") String url,
      LocalPreferences initializedPreferences) {
    java.util.Objects.requireNonNull(initializedPreferences);
    var frequency =
        new io.lexiflow.enrichment.application.preferences.RecentHintFrequency(
            new io.lexiflow.enrichment.platform.preferences.SqliteRecentHintFrequencyStore(url),
            java.time.Clock.systemDefaultZone());
    try {
      frequency.prune();
    } catch (RuntimeException unavailable) {
      java.util.logging.Logger.getLogger(ApiApplication.class.getName())
          .warning("recent hint frequency pruning unavailable; caption service remains available");
    }
    return frequency;
  }

  /** 装配私有机器台账；可读字幕流由独立调试入口输出，避免重复打印 JSON。 */
  @Bean
  SegmentAnalysisLog segmentAnalysisLog(
      @Value("${lexiflow.segment-analysis.enabled:false}") boolean enabled,
      @Value("${lexiflow.segment-analysis.path:}") String path) {
    if (!enabled) return new SegmentAnalysisLog(SegmentAnalysisStore.disabled());
    var resolvedPath = path.isBlank() ? configuredAnalysisPath() : Path.of(path);
    return new SegmentAnalysisLog(new FileSegmentAnalysisStore(resolvedPath, null));
  }

  private static Path configuredAnalysisPath() {
    var configured = System.getenv("LEXIFLOW_SEGMENT_LOG_PATH");
    if (configured != null && !configured.isBlank()) return Path.of(configured);
    return locateRepositoryRoot(Path.of("")).resolve("tmp/analysis/caption-segments.jsonl");
  }

  static Path locateRepositoryRoot(Path start) {
    for (var current = start.toAbsolutePath().normalize();
        current != null;
        current = current.getParent()) {
      if (Files.isRegularFile(current.resolve("AGENTS.md"))
          && Files.isRegularFile(current.resolve("backend/settings.gradle.kts"))
          && Files.isDirectory(current.resolve("backend/product/api"))) return current;
    }
    throw new IllegalStateException("LexiFlow repository root is required for analysis log");
  }
}
