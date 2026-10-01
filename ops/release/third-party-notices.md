# 发布清单许可材料合同

`manifest.mjs` 将发布负责人提供的许可材料来源与随包 notice 文件绑定到确定的相对路径、字节数和 SHA-256。它只验证字段、来源 URL 形式和字节摘要，不判定 SPDX 标识是否真实、材料是否完整或组件是否获准再分发。发布负责人须先核实 LexiFlow、扩展第三方依赖、API 运行时、PostgreSQL、dataset 及其他打包依赖的许可与再分发权；仓库缺少依据时不得推断许可。

`package.mjs` 仅复制描述符 `artifacts` 中显式列出的普通文件，并附加规范化后的 `manifest.json` 与 `manifest.json.sha256`；不会扫描或复制描述符未列出的内容。被引用的 notice 必须以 `role: "license"` 明确列入制品记录。描述符和合成测试中的 `UNVERIFIED-*` 标识不是许可结论。
