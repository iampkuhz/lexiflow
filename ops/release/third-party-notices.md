# 1. 发布清单许可材料合同

`ops/release/integrity/release-artifact-manifest.mjs` 校验许可字段、来源 URL 形式、notice 的路径/字节/SHA-256，以及自包含源码归档内原件和索引的精确对应；这些完整性检查不判定 SPDX 真实性或再分发权。发布负责人须核实 LexiFlow、扩展依赖、API 运行时、SQLite JDBC、dataset 及其他随包依赖的许可；缺依据不得推断获准。

`ops/release/build/package-release-artifacts.mjs` 只复制 `artifacts` 明列的普通文件，并附加规范化的 `manifest.json` 和 `manifest.json.sha256`；manifest 只接受 `schemaVersion=2`。notice 的原始身份保留在 `licenses`，实物统一置于 `role: "corresponding-sources"` 的自包含归档，不在候选根重复存放；其 `metadata.items` 与 `index.json` 绑定 `materials/<noticePath>` 中的每份原件。`UNVERIFIED-*` 和合成测试材料不是许可结论。

正式晋升要求 `ops/release/distribution-licenses.json` 的 `licenses` 与候选许可条目逐项一致，包括来源和 notice 摘要。负责人核实真实再分发权后才能更新该表并形成干净源码候选；空表表示未核准，程序不自动批准。

LexiFlow 自有代码按仓库根 `LICENSE` 使用 MIT，版权主体为 `LexiFlow contributors`。此授权不覆盖第三方代码、容器基础层或资料。自有代码条目的 `noticePath` 为 `LICENSE`；准备 notice 根时须提供与根文件字节及摘要一致的副本。正式候选仍须具备 `LexiFlow`、`API-runtime`、`extension-third-party` 和 `dataset` 全部类别，不能仅凭自有代码条目发布。

## 1.1. 许可集合标识

`LicenseRef-*` 是逐组件原始许可集合的索引，不是新许可或统一授权。源码归档内的 `materials/licenses/LICENSE-COLLECTIONS.txt` 定义这些标识，并指向原样版权、许可、例外、notice 和对应源码。精确文件、摘要和关联以 ledger 与候选 manifest 为准，本文不复制它们。

| 标识 | 原始材料及范围 |
| --- | --- |
| `LicenseRef-API-Base-Component-Licenses` | 固定 API 基底的 Ubuntu 软件包及 Temurin JRE；原始 copyright/common-license/JRE legal、完整 OS DSC 源码组成文件与精确 Temurin 源码分别随包提供。各组件仍适用其自己的 GPL/LGPL、例外、宽松或文档许可条款。 |
`sqlite-jdbc` 3.54.1.0 的 Apache-2.0 和 BSD-2-Clause 原始许可文本从实际依赖 JAR 的 `META-INF/maven/org.xerial/sqlite-jdbc/LICENSE`、`LICENSE.zentus` 原样提取，并分别作为 ledger notice 提供；SQLite 核心本身属于 public domain，声明及来源见 [SQLite Copyright](https://www.sqlite.org/copyright.html)。本发行合同不声称提供独立 SQLite 源码包；不得以此说明替代实际 JAR 内 notice 或其他依赖的许可核对。

API Java 依赖的逐 JAR 坐标、bootJar 内 JAR 摘要、原始许可/notice/POM、精确 source JAR、许可选择及补充 notice 以 `api-java-notices-and-sources.tar.gz` 内 `materials/index.json` 为准；其范围绑定索引中全部第三方运行 JAR 名称与 SHA-256 的规范闭包摘要；完整 API JAR 则由镜像构建身份及候选镜像制品摘要绑定，避免归档摘要与软件身份循环依赖。Logback 与 Jakarta Annotation 采用上游提供的 EPL-2.0 分支，并保留精确 source JAR/POM 与完整发布者源码/构建树；HdrHistogram 采用 CC0-1.0，原额外 BSD-2-Clause 授权文本仍随包保留。归档索引列出完整的第三方 bootJar JAR 名称及 SHA-256；二进制 JAR 本身不在该归档重复携带。该 API Java 集合的许可标识为 Apache-2.0、MIT、CC0-1.0、EPL-2.0 与保留的 BSD-2-Clause 授权。扩展运行时闭包与自有代码的 MIT notice 独立于容器和 Java 组件，构建工具不据此冒充扩展运行时依赖。

镜像中的 `api-java-runtime-notices.tar.gz` 仅保留上述集合的原始许可/notice/POM、逐运行 JAR 身份及完整来源摘要；该小归档不声称包含完整源码。完整 `api-java-notices-and-sources.tar.gz` 仍原样置于公开自包含源码归档，运行镜像无需重复携带源码体。准备 notice 根时从 `ops/release/notices/` 复制受版本控制的小型原件及该运行索引归档，并补齐核准表中的大型外部材料。

## 1.2. 源码与资料的获取

同版本 GitHub Release 单独提供自包含第三方对应源码归档，原始 notice 和对应源码全部位于该归档内；使用者按 `release.json` 的源码资产摘要及归档索引核对原件。GHCR 运行镜像可独立获取，运行时不需要下载或解包大型源码附件。公开附件仍为插件 ZIP、对应源码包和 `release.json`，不新增主安装 tar 或镜像 tar。索引中的上游 URL 表示来源，不能代替已要求随包提供的实物源码。保留源码的补丁和构建配置，不限制上游授予的修改、重建、重新链接或替换权利；读取和解包不要求维护者密钥。分发依据参见 [GPLv2 原文](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)、[GPLv3 原文](https://gcc.gnu.org/onlinedocs/gcc/Copying.html)及 [EPL-2.0 原文](https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html)。

资料只采用固定 ECDICT 来源；保留原 MIT 文本及来源绑定。其维护者针对[商业使用](https://github.com/skywind3000/ECDICT/issues/43#issuecomment-484829135)与[随包离线使用](https://github.com/skywind3000/ECDICT/issues/134#issuecomment-5902723743)的公开答复作为当前核对依据，不扩展为无关资料或第三方权利保证。实际资料仍须通过原 Java 来源审批、导出和 verify，并绑定到最终候选；许可 ledger 不代替这些验证，也不授予 Formal 或公开发行资格。
