package io.lexiflow.lexicon.platform.persistence;

import com.zaxxer.hikari.HikariDataSource;
import io.lexiflow.lexicon.application.LexiconRepository;
import javax.sql.DataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** 统一装配 PostgreSQL 数据源、JdbcClient、事务和词库 Repository。 */
public final class PostgresPersistence implements AutoCloseable {
  private final HikariDataSource ownedDataSource;
  private final LexiconRepository repository;

  private PostgresPersistence(HikariDataSource ownedDataSource, LexiconRepository repository) {
    this.ownedDataSource = ownedDataSource;
    this.repository = repository;
  }

  /**
   * 使用外部拥有的数据源创建词库 Repository。
   *
   * @param dataSource 含义：由调用者生命周期管理的数据源。取值范围：由方法调用前置条件限定。
   * @return 仅暴露应用层合同的词库 Repository
   */
  public static LexiconRepository repository(DataSource dataSource) {
    var jdbc = JdbcClient.create(dataSource);
    var batchJdbc = new JdbcTemplate(dataSource);
    var transaction = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
    return new DefaultLexiconRepository(
        new PostgresLexiconEntryDao(jdbc, batchJdbc),
        new PostgresLexiconImportBatchDao(jdbc),
        new PostgresLexiconEvidenceDao(batchJdbc),
        new LexiconEntryMapper(),
        transaction);
  }

  /**
   * 为离线 CLI 创建并拥有受控的 Hikari 数据源。
   *
   * @param jdbcUrl 含义：PostgreSQL JDBC 连接地址。取值范围：由方法调用前置条件限定。
   * @return 关闭时会一并关闭数据源的持久化装配
   */
  public static PostgresPersistence open(String jdbcUrl) {
    var dataSource = new HikariDataSource();
    dataSource.setJdbcUrl(jdbcUrl);
    return new PostgresPersistence(dataSource, repository(dataSource));
  }

  /**
   * 返回应用层唯一可见的词库持久化合同。
   *
   * @return 已装配的词库 Repository
   */
  public LexiconRepository repository() {
    return repository;
  }

  /**
   * 关闭仅由离线 CLI 创建的数据源。
   *
   * <p>外部数据源不会由本类关闭。
   */
  @Override
  public void close() {
    ownedDataSource.close();
  }
}
