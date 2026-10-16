package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.zaxxer.hikari.HikariDataSource;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;

/** 验证 API PostgreSQL 数据源的轻量池参数与显式配置行为。 */
class PostgresPersistenceConfigurationTest {
  @Test
  void configuresBoundedPoolAndPreservesCredentialsAndCloseBehavior() {
    var context =
        configuredContext(
            Map.of(
                "spring.datasource.url",
                "jdbc:postgresql://localhost:5432/unused",
                "spring.datasource.username",
                "lexiflow-test",
                "spring.datasource.password",
                "test-secret"));
    try (var dataSource = context.getBean(HikariDataSource.class)) {

      assertEquals(1, dataSource.getMinimumIdle());
      assertEquals(4, dataSource.getMaximumPoolSize());
      assertEquals("jdbc:postgresql://localhost:5432/unused", dataSource.getJdbcUrl());
      assertEquals("lexiflow-test", dataSource.getUsername());
      assertEquals("test-secret", dataSource.getPassword());
      assertFalse(dataSource.isClosed());

      // 先验证 Spring 关闭会关闭数据源，再由资源块与 finally 兜底清理失败路径。
      context.close();

      assertTrue(dataSource.isClosed());
    } finally {
      context.close();
    }
  }

  @Test
  void doesNotCreateDataSourceWithoutDatabaseUrl() {
    try (var context = configuredContext(Map.of("spring.datasource.username", "ignored"))) {
      assertTrue(context.getBeansOfType(HikariDataSource.class).isEmpty());
    }
  }

  private static AnnotationConfigApplicationContext configuredContext(
      Map<String, Object> properties) {
    var context = new AnnotationConfigApplicationContext();
    context
        .getEnvironment()
        .getPropertySources()
        .addFirst(new MapPropertySource("test", properties));
    context.register(PostgresPersistenceConfiguration.class);
    context.refresh();
    return context;
  }
}
