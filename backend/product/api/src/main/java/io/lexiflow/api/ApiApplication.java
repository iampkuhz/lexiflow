package io.lexiflow.api;

import io.lexiflow.api.hints.SegmentAnalysisLog;
import io.lexiflow.api.release.ReleaseDatasetCommand;
import io.lexiflow.api.runtime.LexiconHealthIndicator;
import io.lexiflow.api.runtime.LexiconRuntime;
import io.lexiflow.api.runtime.SoftwareIdentity;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistenceConfiguration;
import io.lexiflow.observability.platform.FileSegmentAnalysisStore;
import io.lexiflow.observability.platform.SegmentAnalysisStore;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.nio.file.Path;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;

/** LexiFlow 同步 API 的唯一组合根。 */
@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)
@Import(PostgresPersistenceConfiguration.class)
public class ApiApplication {

  /**
   * 启动 API 进程。
   *
   * @param args 含义：交由 Spring Boot 解析的启动参数。取值范围：非空数组，长度为 0 至任意。
   */
  public static void main(String[] args) {
    if (args.length > 0 && "--release-dataset".equals(args[0])) {
      System.exit(ReleaseDatasetCommand.run(args));
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
      @Value("${lexiflow.runtime.mode:formal}") String mode,
      StructuredEventLogger events) {
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

  /** 装配私有片段台账；仅本机启动器显式启用专用控制台流，普通 logger 不含正文。 */
  @Bean
  SegmentAnalysisLog segmentAnalysisLog(
      @Value("${lexiflow.segment-analysis.enabled:false}") boolean enabled,
      @Value("${lexiflow.segment-analysis.path:}") String path,
      @Value("${lexiflow.segment-analysis.console:false}") boolean console) {
    if (!enabled) return new SegmentAnalysisLog(SegmentAnalysisStore.disabled());
    var resolvedPath = path.isBlank() ? configuredAnalysisPath() : Path.of(path);
    return new SegmentAnalysisLog(
        new FileSegmentAnalysisStore(
            resolvedPath, console ? line -> System.out.print(line) : null));
  }

  private static Path configuredAnalysisPath() {
    var configured = System.getenv("LEXIFLOW_SEGMENT_LOG_PATH");
    if (configured != null && !configured.isBlank()) return Path.of(configured);
    for (var current = Path.of("").toAbsolutePath().normalize();
        current != null;
        current = current.getParent()) {
      if (java.nio.file.Files.isRegularFile(current.resolve("harness/manifest.yaml")))
        return current.resolve("tmp/analysis/caption-segments.jsonl");
    }
    throw new IllegalStateException("LexiFlow repository root is required for analysis log");
  }
}
