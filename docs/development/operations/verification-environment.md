# 1. 隔离验证环境：准备好再执行 Verify

> 位置：[工程地图](../overview.md) → [运行与环境](../operations.md) → 隔离验证环境。输出是可明确指认的测试资源，不是验证 PASS。

## 1.1. 运行时与依赖

首次显式创建 ignored Python 环境并安装声明依赖：

```bash
python3 -m venv .local/lexiflow-python
.local/lexiflow-python/bin/pip install -r requirements-dev.txt
```

后续用该解释器替换命令中的 python3，不依赖系统环境恰好已有 PyYAML。确认 Java 25 与 Wrapper：

```bash
python3 -m scripts.environment.java_exec java -version
python3 -m scripts.environment.java_exec backend/gradlew -p backend --version
```

版本不符时停止并修复配置，不替换锁文件或使用缓存产物冒充本次重建。

## 1.2. 开发库与测试库不能混用

[开发 compose](../../../infra/local/compose.yaml) 拥有固定开发端口和 PostgreSQL 持久卷，用于资料导入与本地体验。它不是临时测试夹具；不要把该数据库 URL 填入测试变量。

完整验证由调用者显式准备唯一命名、无持久卷、随机本机端口的 PostgreSQL 17 与 Redis 7 测试实例。端口和库名从本次资源发现，不从个人 .env 或正在运行的 API 推断；资源创建后等待有界 readiness，执行结束无论成功失败都清理本次拥有的实例。

只有确认隔离后设置：

```bash
export LEXIFLOW_POSTGRES_TEST_JDBC_URL='jdbc:postgresql://127.0.0.1:<test-port>/<isolated-db>?user=postgres'
export LEXIFLOW_REDIS_TEST_ENDPOINT='127.0.0.1:<test-port>'
```

这是输入形状示例，不是可直接粘贴的已存在资源。缺容器运行时、固定镜像、数据库隔离或 readiness 时记录 BLOCKED，不自动改用开发资源。

## 1.3. 从准备回到验证

针对后端完整交付执行 `python3 -m scripts.environment.java_exec backend/gradlew -p backend deliveryFull`；全仓交付回到 [Verify](../change-delivery/verification.md)，由其调用声明的聚合能力，不再重复补跑已包含任务。

deliveryFull 包含 PostgreSQL 集成、runtime smoke 和 boot JAR；普通 check 不代替它。runtime smoke 用合成输入短暂启动 API/worker 后停止，不证明真实页面、生产部署、备份或容量。

记录镜像、资源归属、实际命令和清理结果；URL 中不得含密码或真实数据。更多精确任务见 [Java Reference](../reference/java-checks.md)。
