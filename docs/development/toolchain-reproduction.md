# 1. 工具链复现

复现固定使用仓库声明的 Java 25、Gradle Wrapper、依赖锁和构建配置；系统默认 Java、全局 Gradle、
浮动版本、历史产物和缓存不是可证明输入。记录环境、命令、产物定位、结果和未证明边界；条件不满足时
如实记录 `BLOCKED` 或 `FAIL`。

## 1.1. 运行时、锁与干净构建

```bash
python3 -m scripts.toolchain.java_gradle --version
cd backend && ./gradlew --version
python3 -m scripts.toolchain.java_gradle clean deliveryFull
```

版本不符、网络不可用或依赖缺失时停止，不删除锁文件、不用已有产物替代本次重建。该检查证明工具链重建，
不证明产品旅程、性能或正式 Gate 验收。

## 1.2. 本机数据栈

本机 API/worker 开发栈固定使用 `infra/local/compose.yaml` 的 PostgreSQL 17 与 Redis 7，
绑定 `127.0.0.1:15432` 与 `127.0.0.1:16379`，不读取个人 `.env` 或外部服务。

```bash
python3 scripts/toolchain/local_stack.py up
python3 scripts/toolchain/local_stack.py verify-runtime
python3 scripts/toolchain/local_stack.py down
```

`verify-runtime` 用合成输入短暂启动 API 与 worker 后停止。它不证明生产部署、备份、容量、供应商调用
或 Chrome 扩展旅程。

## 1.3. PostgreSQL 回归环境

唯一 PostgreSQL 回归入口不复用开发栈，也不要求个人数据库：

```bash
python3 scripts/toolchain/postgres_test.py verify --scope all
```

运行器使用 Podman 冷启动一个无数据卷、唯一命名且随机本机端口的 PostgreSQL 17 容器；迁移套件和
带 `postgres` tag 的词库 Persistence 集成测试都连接该真实数据库。结束时容器被删除。 `indexes`、
`versioning` 与 `persistence` 是 Gate 使用的定向 scope，`all` 是完整回归 scope。常规 Gradle
`check` 不运行该 tag，因此不能代替上述命令。

本机或 CI 必须提供可工作的 `podman` 与 `psql`；首次执行会按需拉取固定镜像。缺运行时、客户端、
镜像或就绪能力时，命令返回 `BLOCKED` 并给出恢复路径，不能视为通过。可选
`LEXIFLOW_POSTGRES_TEST_URL` 只作为管理员连接覆盖；运行器仍在该实例中创建、使用并删除唯一测试
数据库，绝不直接迁移覆盖 URL 指向的数据库。不要在命令输出、文档或证据中记录带密码的 URL。

## 1.4. 专项手册

- [离线词库导入](toolchain-reproduction/lexicon-import.md)
- [Chrome 扩展翻译体验与 E2E](toolchain-reproduction/chrome-extension-e2e.md)

当运行时、依赖或官方兼容信息变化时，重新执行受影响检查；历史成功不能替代本次证据。
