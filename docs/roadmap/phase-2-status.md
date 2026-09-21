# 1. 第二阶段交付状态

## 1.1. 当前入口状态

第二阶段的数据合同出口已获得全部所需的三层 Gate 收据；`LF-TSK-DAT-0006@1/1.0.0`
的目录决策为 `PASS`。阶段状态仍为 `ACTIVE`：只有用户给出明确的 G2 批准后，才可将
G2 标记为完成并放行 P3。

| 层次 | 结论 | 收据 run id |
| --- | --- | --- |
| 数据模型验证 | PASS | `587eca17-b66c-4643-9cb5-376f25f4d0f5` |
| 独立审阅 | PASS | `afee2b0e-ce38-4228-b4b0-e5c88a357066` |
| 目录决策 | PASS | `24400a46-789b-44e7-afd3-be94eb7b2194` |

## 1.2. G2 完成边界

G2 的唯一退出任务是 `LF-TSK-DAT-0006`。其 DAT、LEX、CNT 与 SEC 依赖已各自提供所需
的 `PASS` catalog receipt，出口 receipt 也为 `PASS`。这证明 G2 的数据合同、迁移、保留、
删除、恢复和查询预算已按冻结合同验收；它不替代用户对 G2 完成及 P3 放行的明确决定。

## 1.3. 已运行的产品切片

`implement-g2-translation-slice` 已交付一个本机可运行的、确定性词段提示切片：`POST
/api/v1/caption-hints` 接收受长度和范围限制的英文字幕，保留原始英文，并只为目标区间内的
已知词段返回版本绑定的中文释义。未知词返回 `NO_PENDING`，不创建虚假的异步工作，也不调用
模型、供应商、持久化状态或浏览器来源数据。

2026-09-19 已运行 `python3 -m scripts.environment.java_exec backend/gradlew -p backend deliveryFull` 并通过；随后以
合成英文字幕在本机 `127.0.0.1:18080` 进行了 API smoke：已知词返回 `READY` 与中文释义，
未知词返回 `NO_PENDING`，越界范围返回 HTTP `400`。该切片是后续真实数据存储、异步语义和
客户端集成的可替换基础，不改变 G2 的完成边界。

## 1.4. 当前数据与运行时基础

Lexicon、内容修订和字幕片段现在使用稳定 UUID、修订号、规范化文本及 SHA-256 identity；
词义、别名、屈折形和来源信息不可变且版本绑定。PostgreSQL `V001`/`V002` 定义内容、词库、
标注、语义结果和 annotation-work 的表、约束与查询索引。`local_stack.py` 固定以 Podman
启动 PostgreSQL/Redis，并分别验证索引访问路径、迁移版本/恢复、API health 与 worker 启动。

这些实现检查与 P2 依赖图的 validation、review、catalog receipt 已闭合。产品切片仍只是
确定性 API 基础：浏览器采集、异步语义处理及真实持久化接入属于后续阶段，不从该结果推断。
