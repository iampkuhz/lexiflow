# 发布清单许可材料合同

`manifest.mjs` 只校验许可字段、来源 URL 形式及 notice 的路径、字节数和 SHA-256，不判定 SPDX 真实性、材料完整性或再分发权。发布负责人须核实 LexiFlow、扩展依赖、API 运行时、PostgreSQL、dataset 及其他随包依赖的许可；缺依据不得推断获准。

`package.mjs` 只复制 `artifacts` 明列的普通文件，并附加规范化的 `manifest.json` 和 `manifest.json.sha256`；notice 必须以 `role: "license"` 列入制品。`UNVERIFIED-*` 和合成测试材料不是许可结论。

正式晋升要求 `ops/release/distribution-licenses.json` 的 `licenses` 与候选许可条目逐项一致，包括来源和 notice 摘要。负责人核实真实再分发权后才能更新该表并形成干净源码候选；空表表示未核准，程序不自动批准。
