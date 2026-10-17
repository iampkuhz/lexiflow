# 1. 发布清单许可材料合同

`ops/release/integrity/release-artifact-manifest.mjs` 只校验许可字段、来源 URL 形式及 notice 的路径、字节数和 SHA-256，不判定 SPDX 真实性、材料完整性或再分发权。发布负责人须核实 LexiFlow、扩展依赖、API 运行时、PostgreSQL、dataset 及其他随包依赖的许可；缺依据不得推断获准。

`ops/release/build/package-release-artifacts.mjs` 只复制 `artifacts` 明列的普通文件，并附加规范化的 `manifest.json` 和 `manifest.json.sha256`；notice 必须以 `role: "license"` 列入制品。`UNVERIFIED-*` 和合成测试材料不是许可结论。

正式晋升要求 `ops/release/distribution-licenses.json` 的 `licenses` 与候选许可条目逐项一致，包括来源和 notice 摘要。负责人核实真实再分发权后才能更新该表并形成干净源码候选；空表表示未核准，程序不自动批准。

LexiFlow 自有代码按仓库根 `LICENSE` 使用 MIT，版权主体为 `LexiFlow contributors`。此授权不覆盖第三方代码、容器基础层或资料。自有代码条目的 `noticePath` 为 `LICENSE`；准备 notice 根时须提供与根文件字节及摘要一致的副本。正式候选仍须具备 `LexiFlow`、`API-runtime`、`PostgreSQL`、`extension-third-party` 和 `dataset` 全部类别，不能仅凭自有代码条目发布。

## 1.1. 许可集合标识

`LicenseRef-*` 是逐组件原始许可集合的索引，不是新许可或统一授权。发行包中的 `licenses/LICENSE-COLLECTIONS.txt` 定义这些标识，并指向原样版权、许可、例外、notice 和对应源码。精确文件、摘要和关联以 ledger 与候选 manifest 为准，本文不复制它们。

| 标识 | 原始材料及范围 |
| --- | --- |
| `LicenseRef-API-Base-Component-Licenses` | 固定 API 基底的 Ubuntu 软件包及 Temurin JRE；原始 copyright/common-license/JRE legal、完整 OS DSC 源码组成文件与精确 Temurin 源码分别随包提供。各组件仍适用其自己的 GPL/LGPL、例外、宽松或文档许可条款。 |
| `LicenseRef-PostgreSQL-Base-Component-Licenses` | 固定 PostgreSQL 基底中的 PostgreSQL/PGDG 与 Debian 软件包；保留逐组件原文、完整精确 DSC 源码、补丁与构建配置，不把它们统一改为 MIT。 |
| `LicenseRef-PostgreSQL-Container-Extra-Licenses` | 非 dpkg 的 gosu、Go/模块与容器入口脚本；原始 Apache/BSD/MIT 及逐文件条款保留在归档和完整源码中，原始 AUTHORS 同时保留。 |

API Java 依赖的逐 JAR 摘要、许可选择及补充 notice 只在 `api-java-notices-and-sources.tar.gz` 的索引中维护。Logback 与 Jakarta Annotation 使用上游提供的 EPL-2.0 分支，保留原双许可文本、精确 source JAR/POM 与完整发布者源码/构建树；HdrHistogram 使用其提供的 CC0 分支，原额外 BSD 声明保留。扩展运行时闭包与自有代码的 MIT notice 独立于容器和 Java 组件，构建工具不据此冒充扩展运行时依赖。

## 1.2. 源码与资料的获取

完整安装包随同版本 GitHub Release 提供上述 notice 和对应源码归档；从 GHCR 获取镜像的使用者也应从该版本 Release 获取配套完整包，按其 manifest 核对路径和摘要。索引中的上游 URL 表示来源，不能代替已要求随包提供的实物源码。保留源码的补丁和构建配置，不限制上游授予的修改、重建、重新链接或替换权利；读取和解包不要求维护者密钥。分发依据参见 [GPLv2 原文](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)、[GPLv3 原文](https://gcc.gnu.org/onlinedocs/gcc/Copying.html)及 [EPL-2.0 原文](https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html)。

资料只采用固定 ECDICT 来源；保留原 MIT 文本及来源绑定。其维护者针对[商业使用](https://github.com/skywind3000/ECDICT/issues/43#issuecomment-484829135)与[随包离线使用](https://github.com/skywind3000/ECDICT/issues/134#issuecomment-5902723743)的公开答复作为当前核对依据，不扩展为无关资料或第三方权利保证。实际资料仍须通过原 Java 来源审批、导出和 verify，并绑定到最终候选；许可 ledger 不代替这些验证，也不授予 Formal 或公开发行资格。
