package io.lexiflow.lexicon.platform.persistence;

import com.zaxxer.hikari.HikariDataSource;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Objects;
import java.util.Properties;
import javax.sql.DataSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.AbstractDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** 统一装配 SQLite 数据源、事务和公开词库 Repository。 */
public final class SqlitePersistence implements AutoCloseable {
  private final HikariDataSource ownedDataSource;
  private final LexiconRepository repository;

  private SqlitePersistence(HikariDataSource ownedDataSource, LexiconRepository repository) {
    this.ownedDataSource = ownedDataSource;
    this.repository = repository;
  }

  /**
   * 使用外部拥有的数据源创建词库持久化入口，不接管数据源生命周期。
   *
   * @param dataSource 含义：调用者持有的数据源；取值范围：非空且连接可应用安全设置
   */
  public static LexiconRepository repository(DataSource dataSource) {
    return repository(dataSource, 0);
  }

  /**
   * 使用外部数据源创建词库 Repository，并保留受控 rebuild 前一版本下界。
   *
   * @param dataSource 含义：调用者持有的数据源；取值范围：非空且连接可应用安全设置
   * @param previousVersionFloor 含义：重建前已发布版本的下界；取值范围：非负整数
   */
  public static LexiconRepository repository(DataSource dataSource, long previousVersionFloor) {
    Objects.requireNonNull(dataSource, "dataSource");
    if (previousVersionFloor < 0)
      throw new IllegalArgumentException("version floor must be non-negative");
    var configured = new ConfiguringDataSource(dataSource);
    var jdbc = new JdbcTemplate(configured);
    var transaction = new TransactionTemplate(new DataSourceTransactionManager(configured));
    return new DefaultLexiconRepository(jdbc, transaction, previousVersionFloor);
  }

  /**
   * 为离线 CLI 创建并拥有单连接 SQLite 数据源。
   *
   * <p>目标文件必须已存在；只有显式初始化可创建数据库文件。
   *
   * @param jdbcUrl 含义：离线写入目标；取值范围：已存在的绝对规范文件地址，不允许只读模式
   */
  public static SqlitePersistence open(String jdbcUrl) {
    validateDatabaseUrl(jdbcUrl, true);
    if (readOnly(jdbcUrl))
      throw new IllegalArgumentException("write datasource cannot use read-only mode");
    var pool = new HikariDataSource();
    pool.setJdbcUrl(driverUrl(jdbcUrl));
    pool.setMinimumIdle(1);
    pool.setMaximumPoolSize(1);
    configurePool(pool);
    pool.addDataSourceProperty("transaction_mode", "IMMEDIATE");
    return new SqlitePersistence(pool, repository(pool));
  }

  static void configurePool(HikariDataSource pool) {
    pool.addDataSourceProperty("foreign_keys", "true");
    pool.addDataSourceProperty("busy_timeout", "5000");
    pool.addDataSourceProperty("synchronous", "FULL");
    pool.addDataSourceProperty("journal_mode", "DELETE");
  }

  /**
   * 打开已存在的数据库文件，并应用、验证每条连接的安全设置。
   *
   * @param jdbcUrl 含义：目标数据库文件地址；取值范围：受限的绝对规范文件地址，可显式只读
   */
  public static Connection connect(String jdbcUrl) throws SQLException {
    validateDatabaseUrl(jdbcUrl, true);
    var properties = new Properties();
    properties.setProperty("transaction_mode", readOnly(jdbcUrl) ? "DEFERRED" : "IMMEDIATE");
    var connection = DriverManager.getConnection(driverUrl(jdbcUrl), properties);
    try {
      configure(connection);
      return connection;
    } catch (SQLException | RuntimeException failure) {
      try {
        connection.close();
      } catch (SQLException closeFailure) {
        failure.addSuppressed(closeFailure);
      }
      throw failure;
    }
  }

  /**
   * 对借出的数据库连接应用安全设置；不改写已经开始的事务。
   *
   * @param connection 含义：待配置及验证的连接；取值范围：非空、已打开且支持规定的连接设置
   */
  public static void configure(Connection connection) throws SQLException {
    Objects.requireNonNull(connection, "connection");
    boolean activeTransaction = !connection.getAutoCommit();
    try (Statement statement = connection.createStatement()) {
      if (!activeTransaction) statement.execute("PRAGMA foreign_keys=ON");
      statement.execute("PRAGMA busy_timeout=5000");
      if (!activeTransaction) {
        statement.execute("PRAGMA synchronous=FULL");
        try (var current = statement.executeQuery("PRAGMA journal_mode")) {
          if (!current.next()) throw new SQLException("SQLite journal_mode is unavailable");
          if (!"delete".equalsIgnoreCase(current.getString(1))) {
            try (var changed = statement.executeQuery("PRAGMA journal_mode=DELETE")) {
              if (!changed.next() || !"delete".equalsIgnoreCase(changed.getString(1))) {
                throw new SQLException("SQLite journal_mode could not be set to DELETE");
              }
            }
          }
        }
      }
      requirePragma(statement, "foreign_keys", 1);
      requirePragma(statement, "busy_timeout", 5000);
      requirePragma(statement, "synchronous", 2);
      try (var result = statement.executeQuery("PRAGMA journal_mode")) {
        if (!result.next() || !"delete".equalsIgnoreCase(result.getString(1))) {
          throw new SQLException("SQLite journal_mode must be DELETE");
        }
      }
    }
  }

  private static void requirePragma(Statement statement, String pragma, int expected)
      throws SQLException {
    try (var result = statement.executeQuery("PRAGMA " + pragma)) {
      if (!result.next() || result.getInt(1) != expected) {
        throw new SQLException("SQLite PRAGMA " + pragma + " has an unexpected value");
      }
    }
  }

  /**
   * 校验目标是绝对规范文件地址，并拒绝符号链接及不受支持的连接选项。
   *
   * @param jdbcUrl 含义：待验证的目标地址；取值范围：仅允许受限的文件型数据库地址
   * @param requireExisting 含义：是否必须已有目标文件；取值范围：真时要求已有普通文件，假时允许规范父目录下的新文件
   */
  public static void validateDatabaseUrl(String jdbcUrl, boolean requireExisting) {
    Path path = databaseFilePath(jdbcUrl);
    try {
      if (requireExisting) {
        if (!Files.isRegularFile(path))
          throw new IllegalArgumentException("SQLite database file does not exist");
        if (!path.toRealPath().equals(path))
          throw new IllegalArgumentException(
              "SQLite database path must be canonical and symlink-free");
      } else if (Files.exists(path)) {
        if (!Files.isRegularFile(path) || !path.toRealPath().equals(path)) {
          throw new IllegalArgumentException(
              "SQLite database path must be a canonical regular file");
        }
      } else {
        Path parent = path.getParent();
        if (parent == null || !Files.isDirectory(parent) || !parent.toRealPath().equals(parent)) {
          throw new IllegalArgumentException(
              "new SQLite database must be created in an existing canonical directory");
        }
      }
    } catch (java.io.IOException exception) {
      throw new IllegalArgumentException("SQLite database file is unavailable", exception);
    }
  }

  /**
   * 从受限连接地址提取绝对规范文件路径，不打开或创建目标。
   *
   * @param jdbcUrl 含义：待解析的连接地址；取值范围：绝对文件地址，仅允许规定的打开模式
   */
  public static Path databaseFilePath(String jdbcUrl) {
    if (jdbcUrl == null || !jdbcUrl.startsWith("jdbc:sqlite:")) {
      throw new IllegalArgumentException("JDBC URL must identify a SQLite database file");
    }
    String value = jdbcUrl.substring("jdbc:sqlite:".length());
    int query = value.indexOf('?');
    String raw = query < 0 ? value : value.substring(0, query);
    String options = query < 0 ? "" : value.substring(query + 1);
    if (options.contains("&")
        || options.contains(";")
        || options.contains("#")
        || (!options.isEmpty()
            && !options.equals("mode=ro")
            && !options.equals("mode=rw")
            && !options.equals("mode=rwc"))) {
      throw new IllegalArgumentException("SQLite URL has unsupported options");
    }
    if (raw.startsWith("file:")) raw = raw.substring("file:".length());
    if (raw.isBlank() || raw.contains(":memory:") || raw.contains("..")) {
      throw new IllegalArgumentException("SQLite URL must identify a normalized database file");
    }
    Path path = Path.of(raw);
    if (!path.isAbsolute() || !path.normalize().equals(path) || path.getFileName() == null) {
      throw new IllegalArgumentException("SQLite database path must be absolute and normalized");
    }
    return path;
  }

  private static boolean readOnly(String jdbcUrl) {
    return jdbcUrl.contains("?mode=ro");
  }

  /**
   * 将外部路径合同转换为驱动可识别的文件地址，并固定已有文件的打开模式。
   *
   * @param jdbcUrl 含义：外部调用者提供的地址；取值范围：符合受限绝对文件地址合同的字符串
   */
  public static String driverUrl(String jdbcUrl) {
    Path path = databaseFilePath(jdbcUrl);
    String mode = readOnly(jdbcUrl) ? "ro" : "rw";
    return "jdbc:sqlite:" + path.toUri().toASCIIString() + "?mode=" + mode;
  }

  /**
   * 校验文件地址并判断是否明确指定只读模式。
   *
   * @param jdbcUrl 含义：待判断的连接地址；取值范围：符合受限绝对文件地址合同的字符串
   */
  public static boolean isReadOnlyUrl(String jdbcUrl) {
    databaseFilePath(jdbcUrl);
    return readOnly(jdbcUrl);
  }

  /** 每次借用连接时验证 SQLite PRAGMA；不改变已处于事务中的连接状态。 */
  private static final class ConfiguringDataSource extends AbstractDataSource {
    private final DataSource delegate;

    private ConfiguringDataSource(DataSource delegate) {
      this.delegate = delegate;
    }

    @Override
    public Connection getConnection() throws SQLException {
      return configureBorrowed(delegate.getConnection());
    }

    @Override
    public Connection getConnection(String user, String password) throws SQLException {
      return configureBorrowed(delegate.getConnection(user, password));
    }

    private Connection configureBorrowed(Connection connection) throws SQLException {
      try {
        configure(connection);
        return connection;
      } catch (SQLException | RuntimeException failure) {
        try {
          connection.close();
        } catch (SQLException closeFailure) {
          failure.addSuppressed(closeFailure);
        }
        throw failure;
      }
    }
  }

  /**
   * 返回应用层唯一可见的词库持久化入口。
   *
   * @return 非空的词库持久化入口，与本实例共用数据源且不扩大领域访问权限
   */
  public LexiconRepository repository() {
    return repository;
  }

  /**
   * 关闭本实例拥有的单连接池。
   *
   * <p>调用者负责在最后一次离线持久化操作完成后关闭本实例。
   */
  @Override
  public void close() {
    ownedDataSource.close();
  }
}
