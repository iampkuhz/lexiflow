# 发布清单许可材料合同

`manifest.mjs` 将发布负责人提供的许可材料来源与随包 notice 文件绑定到确定的相对路径、字节数和 SHA-256。它只验证字段、来源 URL 形式和字节摘要，不判定 SPDX 标识是否真实、材料是否完整或组件是否获准再分发。发布负责人须先核实 LexiFlow、扩展第三方依赖、API 运行时、PostgreSQL、dataset 及其他打包依赖的许可与再分发权；仓库缺少依据时不得推断许可。

`package.mjs` 仅复制描述符 `artifacts` 中显式列出的普通文件，并附加规范化后的 `manifest.json` 与 `manifest.json.sha256`；不会扫描或复制描述符未列出的内容。被引用的 notice 必须以 `role: "license"` 明确列入制品记录。描述符和合成测试中的 `UNVERIFIED-*` 标识不是许可结论。


正式晋升还要求 `ops/release/distribution-licenses.json` 的 `licenses` 与候选 manifest 的许可条目逐项完整一致，包括来源和 notice 摘要。该表只能在负责人核实真实再分发权后更新，再形成干净源码候选；初始空表表示未核准，不是缺省同意。程序不会自动把 `UNVERIFIED-*`、测试材料或字段校验通过改成许可批准。
