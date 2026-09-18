# 1. 校验：架构与图表

## 1.1. 人工核对

先读[架构总览](../../architecture/overview.md)，能明确回答：英文字幕为何不等待网络/模型、Rules 与 Models 的分工、为何没有账号/学习档案、以及 `api`/`worker` 为何仍是模块化单体。回答必须能回指正文，而非猜测。

| 问题 | 对应专题 |
| --- | --- |
| 数据 owner 和依赖方向 | [模块边界](../../architecture/boundaries.md) 与 [机器边界](../../../harness/module-boundaries.yaml) |
| 字幕、超时与乱序 | [流程](../../architecture/flows.md) |
| 授权、缓存、删除与降级 | [运行安全](../../architecture/runtime-safety.md) |
| 已确定的架构结论及取舍 | [架构决策](../../architecture/decisions.md) |

用“正常提示、模型超时后换字幕、旧结果晚到”三种情形检查负责人、版本和终态；这只是语义审查，不是端到端测试。

## 1.2. 图源

图的唯一提交图源是正文 `plantuml` 围栏。修改图时先按[Harness 图表工作流](../../../harness/README.md)在 `tmp/diagrams/` 校验、渲染并人工预览，再原样复制回正文；缺工具或无法预览记 `BLOCKED`。不要把 `.puml`、SVG、PNG 或旧图片当作正文证据。

## 1.3. 静态检查

```bash
python3 -m scripts.harness.docs_check
python3 -m scripts.harness.policy_projection --check
git diff --check
git diff --cached --check
```

这些检查不证明图像布局、中文语义或正式验收。图文矛盾、owner 不明或关键失败分支缺失为 `FAIL`；所需材料或预览缺失为 `BLOCKED`。
