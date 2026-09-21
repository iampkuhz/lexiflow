# 1. 工具链复现

复现固定使用仓库声明的 Java 25、Gradle Wrapper、依赖锁和构建配置；系统默认 Java、全局 Gradle、
浮动版本、历史产物和缓存不是可证明输入。记录环境、命令、产物定位、结果和未证明边界；条件不满足时
如实记录 `BLOCKED` 或 `FAIL`。

## 1.1. 运行时、锁与干净构建

```bash
python3 -m scripts.environment.java_exec java -version
python3 -m scripts.environment.java_exec backend/gradlew -p backend --version
python3 -m scripts.environment.java_exec backend/gradlew -p backend clean deliveryFull
```

版本不符、网络不可用或依赖缺失时停止，不删除锁文件、不用已有产物替代本次重建。该检查证明工具链重建，
不证明产品旅程、性能或正式 Gate 验收。

## 1.2. 本机数据栈

本机 API/worker 开发栈固定使用 `infra/local/compose.yaml` 的 PostgreSQL 17 与 Redis 7，
绑定 `127.0.0.1:15432` 与 `127.0.0.1:16379`，不读取个人 `.env` 或外部服务。

```bash
podman compose -f infra/local/compose.yaml up -d
export LEXIFLOW_POSTGRES_TEST_JDBC_URL='jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres'
export LEXIFLOW_REDIS_TEST_ENDPOINT='127.0.0.1:16379'
python3 -m scripts.environment.java_exec backend/gradlew -p backend deliveryFull
podman compose -f infra/local/compose.yaml down
```

`verify-runtime` 用合成输入短暂启动 API 与 worker 后停止。它不证明生产部署、备份、容量、供应商调用
或 Chrome 扩展旅程。

## 1.3. PostgreSQL 回归环境

PostgreSQL 回归只接受显式隔离的测试地址，不读取个人数据库配置：

```bash
export LEXIFLOW_POSTGRES_TEST_JDBC_URL='jdbc:postgresql://127.0.0.1:<random-port>/<isolated-db>?user=postgres'
export LEXIFLOW_REDIS_TEST_ENDPOINT='127.0.0.1:<random-port>'
python3 -m scripts.environment.java_exec backend/gradlew -p backend deliveryFull
```

调用者负责用固定镜像创建无数据卷、唯一命名且随机本机端口的 PostgreSQL 17 与 Redis 7 测试资源，
并在 `finally` 清理。`deliveryFull` 运行迁移、带 `postgres` tag 的 Persistence 集成测试和跨进程
runtime smoke；常规 Gradle `check` 不代替该完整入口。

本机或 CI 缺运行时、固定镜像或就绪能力时记录 `BLOCKED`，不能视为通过。不得让测试地址指向开发库，
也不要在命令输出、文档或证据中记录带密码的 URL。

## 1.4. 专项手册

- [离线词库导入](toolchain-reproduction/lexicon-import.md)
- [Chrome 扩展翻译体验与 E2E](toolchain-reproduction/chrome-extension-e2e.md)

当运行时、依赖或官方兼容信息变化时，重新执行受影响检查；历史成功不能替代本次证据。
