package io.lexiflow.lexicon.platform.persistence;

import com.zaxxer.hikari.HikariDataSource;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** API 与 worker 共用的、显式属性驱动的 PostgreSQL 持久化装配。 */
@Configuration(proxyBeanMethods = false)
public class PostgresPersistenceConfiguration {
  /** 仅在配置 JDBC URL 时创建 Hikari 数据源，使演示运行不依赖数据库。 */
  @Bean(destroyMethod = "close")
  @ConditionalOnProperty(name = "spring.datasource.url")
  HikariDataSource lexiflowDataSource(
      @Value("${spring.datasource.url}") String url,
      @Value("${spring.datasource.username:}") String username,
      @Value("${spring.datasource.password:}") String password) {
    var dataSource = new HikariDataSource();
    dataSource.setJdbcUrl(url);
    if (!username.isBlank()) {
      dataSource.setUsername(username);
    }
    if (!password.isBlank()) {
      dataSource.setPassword(password);
    }
    return dataSource;
  }

  /** 将 PostgreSQL DAO 组合为只向上层暴露的 Repository。 */
  @Bean
  @ConditionalOnBean(DataSource.class)
  LexiconRepository lexiconRepository(DataSource dataSource) {
    return PostgresPersistence.repository(dataSource);
  }
}
