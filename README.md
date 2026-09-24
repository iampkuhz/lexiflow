# LexiFlow

在 YouTube 英文字幕的词语后提供来自已发布词库的简短中文提示，例如 `assignments(作业)`。**英文立即显示；观看请求不调用模型，也不等待事后分析。**

## 本地启动

准备 Java 25、Node.js/npm、Chrome 与已发布 PostgreSQL 词库后，从仓库根启动 API：

```bash
python3 -m scripts.environment.start_api --database-url 'jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres'
```

在另一个终端构建扩展，然后在 Chrome 加载 `extension/dist`：

```bash
npm --prefix extension ci
npm --prefix extension run build
```

完整的环境检查、端口确认、词库判断与安装步骤见[本地体验](docs/development/operations/local-experience.md)。不带数据库参数只使用有限演示词库，不是完整词库体验。命令不会自动导入资料；资料准备见[离线词库导入](docs/development/operations/lexicon-import.md)。

## 从哪里继续

- **理解产品与系统：**[产品说明](docs/product/product-brief.md) → [架构总览](docs/architecture/overview.md)。
- **修改与交付：**[工程地图](docs/development/overview.md) → [开发交付](docs/development/change-delivery.md)。
- **没有效果或检查失败：**[按阶段排障](docs/development/troubleshooting.md)。
- **完整导航与当前状态：**[文档首页](docs/README.md)、[路线图状态](docs/roadmap/master-plan/status.md)。

规则真源为 [AGENTS.md](AGENTS.md) 与 [Harness](harness/README.md)；它们不是用户操作手册。
