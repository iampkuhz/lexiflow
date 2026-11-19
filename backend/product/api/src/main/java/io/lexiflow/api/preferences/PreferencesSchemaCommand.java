package io.lexiflow.api.preferences;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;

/** 仅在显式命令调用时初始化独立偏好数据库。 已存在的目标文件保持不变；普通 API 启动不会调用此初始化逻辑。 */
public final class PreferencesSchemaCommand {
  /** 禁止创建无初始化参数的命令实例。 */
  private PreferencesSchemaCommand() {}

  /**
   * 创建缺失的偏好数据库及其受信任 schema。
   *
   * @param args 含义：命令行参数。取值范围：恰好一个指向专属 SQLite 文件的 jdbc:sqlite: URL。
   * @return 初始化成功或目标已存在时为 0。
   */
  public static int run(String[] args) {
    if (args.length != 1 || !args[0].startsWith("jdbc:sqlite:"))
      throw new IllegalArgumentException("usage: --init-preferences <jdbc-url>");
    var path = Path.of(args[0].substring("jdbc:sqlite:".length()).split("\\?", 2)[0]);
    if (Files.exists(path)) {
      if (!Files.isRegularFile(path) || Files.isSymbolicLink(path))
        throw new IllegalStateException("preferences database target is unsafe");
      System.out.println("PASS preferences database already exists; left unchanged");
      return 0;
    }
    try {
      Files.createDirectories(path.toAbsolutePath().getParent());
      String schema;
      try (var input =
          PreferencesSchemaCommand.class.getResourceAsStream(
              "/META-INF/lexiflow-preferences-schema.sql")) {
        if (input == null)
          throw new IllegalStateException("trusted preferences schema unavailable");
        schema = new String(input.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
      }
      try (var c = DriverManager.getConnection(args[0]);
          var s = c.createStatement()) {
        for (String sql : schema.split(";")) if (!sql.isBlank()) s.execute(sql);
      }
      try {
        Files.setPosixFilePermissions(
            path,
            java.util.Set.of(
                java.nio.file.attribute.PosixFilePermission.OWNER_READ,
                java.nio.file.attribute.PosixFilePermission.OWNER_WRITE));
      } catch (UnsupportedOperationException ignored) {
        // 不支持该文件权限的测试主机以专属临时目录隔离数据库。
      }
      System.out.println("PASS preferences database initialized");
      return 0;
    } catch (Exception e) {
      throw new IllegalStateException("preferences database initialization failed", e);
    }
  }
}
