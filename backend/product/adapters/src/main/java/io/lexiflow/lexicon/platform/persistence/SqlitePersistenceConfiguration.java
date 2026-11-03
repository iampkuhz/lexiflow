package io.lexiflow.lexicon.platform.persistence;

import com.zaxxer.hikari.HikariDataSource;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** API 应用使用的、显式属性驱动的 SQLite 持久化装配。 */
@Configuration(proxyBeanMethods = false)
public class SqlitePersistenceConfiguration {
  /** 仅为已经存在的 mode=ro SQLite 文件创建只读 Hikari 数据源。 */
  @Bean(destroyMethod = "close")
  @ConditionalOnProperty(name = "spring.datasource.url")
  HikariDataSource lexiflowDataSource(
      @Value("${spring.datasource.url}") String url,
      @Value("${spring.datasource.username:}") String username,
      @Value("${spring.datasource.password:}") String password) {
    if (!username.isBlank() || !password.isBlank()) {
      throw new IllegalArgumentException("SQLite persistence does not accept credentials");
    }
    SqlitePersistence.validateDatabaseUrl(url, true);
    if (!SqlitePersistence.isReadOnlyUrl(url)) {
      throw new IllegalArgumentException("API SQLite URL must explicitly use mode=ro");
    }
    var dataSource = new HikariDataSource();
    dataSource.setMinimumIdle(1);
    dataSource.setMaximumPoolSize(4);
    dataSource.setJdbcUrl(SqlitePersistence.driverUrl(url));
    SqlitePersistence.configurePool(dataSource);
    dataSource.addDataSourceProperty("transaction_mode", "DEFERRED");
    return dataSource;
  }

  /** 将 SQLite DAO 组合为只向上层暴露的 Repository。 */
  @Bean
  @ConditionalOnBean(DataSource.class)
  LexiconRepository lexiconRepository(DataSource dataSource) {
    return SqlitePersistence.repository(dataSource);
  }
}
