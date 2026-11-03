# 1. 发布与使用闭环

上级：[第二阶段](../phase2.md)。本页定义产品交付目标，不记录执行通过状态；证据见[状态页](status.md)。操作步骤只在[Docker 生命周期指南](../../../scenarios/docker-release.md)、[本机开发](../../../scenarios/local-development.md)和[发行场景](../../../scenarios/github-actions.md)维护。

## 1.1. 两种运行形式

macOS 源码调试与 Docker 发行使用同一 Java 25 Modular Monolith、唯一 API 和 SQLite 事实存储，不新增 worker 或第二套提示算法。API 不在缺库时隐式创建或清空资料。Redis 只按真实消费者使用，不因开发配置存在而成为发行必需服务。

发行单运行镜像只包含 Java API、SQLite JDBC 和已发布资料；专属持久卷保存 `lexicon.db`，不依赖独立数据库服务。初始化完成后再启动只读 API，只有完整资料、正确软件/发布身份和就绪探测全部成立才健康。宿主仅需 Docker 与 Chrome，不需 Java、Node、Python、Gradle 或原始 CSV。

API 默认只发布到宿主 loopback；loopback 限制入站不代表禁止出站，产品不新增数据外发、账号或偏好同步。扩展即时显示英文，服务缺失只影响中文提示。

## 1.2. 发行产物与版本

单一软件版本源驱动后端与扩展。发布清单独立绑定源码、软件/API/SQL、数据生成、内容、资料发布身份和精确制品摘要，版本不能相互替代。每平台只交付一个匹配运行镜像，另含资料包、最新 SQL、扩展 ZIP、入口与真实许可材料。来源包、对应源码包不是数据库占用；许可完整性不能被瘦身削弱。

当期原生运行证明使用 Apple Silicon macOS 与 `linux/arm64`；其他平台构建成功不自动获得运行资格。扩展 ZIP 不等于商店发行。私有候选构建、独立正式验收和人类批准后精确晋升相互分离；不得自动创建正式 tag、公开镜像或 GitHub Release。

## 1.3. 用户流程与故障恢复

取得可信制品 → 核对完整匹配身份 → 准备本安装专属数据空间 → 离线验证并初始化 SQLite → 启动只读 API → 检查就绪 → 安装匹配扩展。

更新先验证完整候选及新独立资料空间，健康后切换；失败保留原组合与受控恢复入口，不在旧运行卷隐式清库。仅源码调试的显式重建可重建本项目表并完整重导，失败回滚。升级不复用仍绑定不同内容的资料身份；浏览器本机抑制偏好不删除。

备份通过同一事务快照导出和校验的数据包，不直接复制运行中的数据库主文件。恢复严格核对 schema、来源许可、生成、内容和发布身份。停止不删除卷；卸载对程序、数据、敏感记录和浏览器偏好分别处理。任何操作都不接管未知目录、对象、容器、卷或符号链接。

## 1.4. 实施工作与交付目标

交付不仅是 Dockerfile 或 ZIP：必须包含匹配构建、来源许可、离线初始化、健康检查、安装重启、故障恢复、完整查询行为及同输入资源量化证据。使用者可区分未启动、初始化中、资料不可用、协议不匹配及已就绪；失败不冒充成功。

任务及精确文件范围由 [Catalog](../../../../planning/workstreams.yaml) 管理；当前存储合同见[数据模型](../../../system/lexicon/data-model.md)，运行步骤不在路线图重复维护。

## 1.5. 资源验收

二期采用以下工程验收上限，不是已达到的实测成绩，也不是建议用户采购的硬件配置。基准运行环境为 4 个逻辑 CPU、8 GiB 内存、SSD，给 Docker 中的 API 与 SQLite 文件缓存合计不超过 2 GiB 内存；宿主运行 Chrome 观看一条英文视频，不要求 GPU 或模型。记录 OS、Docker 版本、CPU 架构和资源限制，在 Apple Silicon macOS Docker 的原生 `linux/arm64` 环境测量；模拟架构的构建成功不能证明原生性能。

| 项目 | 上限与测量条件 |
| --- | --- |
| 首次发行下载 | 单运行镜像、压缩资料及扩展合计不超过 3 GiB，按制品字节计，不把带宽换算成固定下载时间 |
| 日常安装磁盘 | 全部运行镜像、展开资料和一个活动数据空间合计不超过 8 GiB；保留一次更新恢复空间时合计不超过 16 GiB |
| 首次资料初始化 | 制品已在本机、空的隔离资料空间，开始初始化至正式就绪不超过 10 分钟；不计网络下载 |
| 日常冷启动 | 已初始化资料、进程全部停止后启动至正式就绪不超过 30 秒；不是空库初始化 |
| 服务内存 | API 与 SQLite 页缓存的总 cgroup 内存峰值不超过 2 GiB，包括文件缓存；不只报告 JVM heap |
| 空闲 CPU | 就绪后静置 60 秒，再测 120 秒，总 CPU 平均不超过 0.05 个逻辑核 |
| 播放 CPU | 单客户端连续至少 375 秒合成字幕（每秒一次变化），总 CPU 平均不超过 1 个逻辑核，同时记录峰值 |
| 稳态提示处理 | 同一播放样本预热 30 秒后，后端端到端耗时 p95 不超过 150 ms；错误/超时计入失败，不从统计中静默剔除 |

分别记录冷/热状态、样本数、失败数和分母。真实视频体验另行验证，不用上述合成样本宣称普遍语义正确率。实测超限应先分析并修复，不得由实施者直接放宽预算；必需平台或预算缺证据为 BLOCKED。Docker 本身仍是使用者需安装的运行环境，不宣称零环境依赖。


## 1.6. Catalog 任务与依赖

稳定 Task ID 不因存储引擎改变而复用或改名。各 Task 的最新范围、版本、必需检查和真实依赖以 Catalog 为准；以下仅导航其实际职责。

### 1.6.1. LF-TSK-ARCH-2006 · 冻结双运行形式、发布身份和使用闭环合同

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.2. LF-TSK-OPS-2003 · 建立软件版本真源并接入后端构建

统一软件版本及内嵌身份由构建入口产生；应用、生成、内容与发布身份各自核验。详见[版本与发行](../../../scenarios/github-actions.md)。

#### 1.6.2.1. 版本与发行输入合同

统一软件版本及内嵌身份由构建入口产生；应用、生成、内容与发布身份各自核验。详见[版本与发行](../../../scenarios/github-actions.md)。

### 1.6.3. LF-TSK-EXT-2003 · 让扩展构建消费统一版本并产出匹配扩展包

统一软件版本及内嵌身份由构建入口产生；应用、生成、内容与发布身份各自核验。详见[版本与发行](../../../scenarios/github-actions.md)。

#### 1.6.3.1. 扩展构建与 ZIP 合同

扩展构建只消费统一身份并产出匹配 ZIP；界面展示服务和资料状态，拒绝协议不匹配。详见[扩展验收](../../../scenarios/extension-e2e.md)。

### 1.6.4. LF-TSK-OPS-2004 · 实现发布清单、摘要和许可材料打包

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.4.1. 清单、许可与目录装配合同

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.5. 已发布资料快照与隔离初始化

同一 API JAR 提供资料 export/verify/initialize；封闭包包含 manifest、来源审批和三份最新 NDJSON。恢复只写显式目标文件并校验完整事务，不执行外来 SQL。详见[词库导入](../../../scenarios/lexicon-import.md)。

#### 1.6.5.1. Java 快照包与安全初始化

同一 API JAR 提供资料 export/verify/initialize；封闭包包含 manifest、来源审批和三份最新 NDJSON。恢复只写显式目标文件并校验完整事务，不执行外来 SQL。详见[词库导入](../../../scenarios/lexicon-import.md)。

#### 1.6.5.2. ECDICT 精简来源输入

同一 API JAR 提供资料 export/verify/initialize；封闭包包含 manifest、来源审批和三份最新 NDJSON。恢复只写显式目标文件并校验完整事务，不执行外来 SQL。详见[词库导入](../../../scenarios/lexicon-import.md)。

### 1.6.6. LF-TSK-API-2003 · 提供发布就绪合同并隔离发行包敏感记录授权

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.6.1. 状态查询与敏感记录开关

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.7. LF-TSK-EXT-2004 · 展示服务与资料状态并拒绝不匹配协议

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.7.1. 协议预检与状态展示

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.8. LF-TSK-OPS-2006 · 封装 SQLite 单运行镜像和 Compose

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.8.1. 容器运行边界

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.9. LF-TSK-OPS-2007 · 实现匹配制品更新与失败恢复操作

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.9.1. 发行入口与离线匹配校验

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.9.2. 私有安装与更新恢复操作

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.10. LF-TSK-OPS-2008 · 串联 Apple Silicon 构建与受控发布流水线

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.10.1. 构建输入与候选产物

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

#### 1.6.10.2. 构建机命令入口

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.11. LF-TSK-ARCH-2007 · 编写双运行入口和用户安装更新指南

固定输入、精确摘要、资源归属、有界执行、失败关闭及受控恢复均保留；运行只使用单 API 与 SQLite 文件，不生成数据库凭据。构建和生命周期参数由实际 CLI 定义，详见[Docker 生命周期](../../../scenarios/docker-release.md)及[发行场景](../../../scenarios/github-actions.md)。

### 1.6.12. LF-TSK-QLT-2002 · 验证干净环境发布闭环和资源门槛

由未参与实现的真实 validator 执行冻结检查，另由不同 reviewer 只读审查。真实容器、资源性能和来源许可证据不能被模拟测试或静态检查替代。详见[开发与验收](../../../scenarios/run-repository-gates.md)。
