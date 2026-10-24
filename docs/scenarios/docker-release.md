# 1. 单镜像发行与容器生命周期

> 普通使用者从[两步使用](../user/podman-macos.md)开始。本页面向维护者，源码准备和正式发布见[GitHub 发行](github-actions.md)。

## 1.1. 交付边界

每个新正式版本只有一个用户运行镜像 `ghcr.io/iampkuhz/lexiflow`，支持 `linux/arm64`，对应 macOS Apple Silicon + Podman。镜像包含 Java 25 API、PostgreSQL、已发布词库和自动初始化，常驻一个容器，无 Redis、外置数据库、socket、特权或容器套容器。唯一 Java 产品应用不因基础设施进程管理新增 worker。

正式 Release 自定义附件恰好三项：Chrome 插件 ZIP、第三方对应源码包、`release.json`。不提供用户主安装 tar、镜像 tar、独立 `.sha256` 或内部候选附件；GitHub 自动源码 ZIP/TAR 不计入三项。正文展示摘要和固定 digest 启动命令，机器只读同一不可变 Release 的 `release.json`，不解析可编辑正文。

## 1.2. 身份与源码对应

`release.json` 绑定构建版本、commit/源码摘要、API/SQL/资料身份、唯一镜像 registry manifest digest 与 config ID、平台，以及扩展和对应源码包的字节数、SHA-256、下载地址和来源对应关系。镜像 config ID 不是 registry manifest digest；用户按后者拉取。

大型第三方对应源码在候选组装时形成独立受验制品；原许可证、来源、摘要和完整对应关系保留。运行镜像保留必要告知。晋升不能删除材料、替换来源或在发布阶段重构建制品以规避合规义务。

## 1.3. 启动与持久化

同一条 `podman run` 命令自动建立并挂载正式命名卷，API 仅映射宿主 loopback 18080，数据库只在本容器内连接。容器入口识别卷归属及格式、等待数据库、执行事务初始化和真实资料核对；初始化状态不等于成功。数据库/初始化/API 的关键失败和异常退出不能留下正式健康状态。

停止用 `podman stop --time 45 lexiflow-release`，启动已有容器用 `podman start lexiflow-release`，均保留卷。名字/端口冲突、未知非空卷或卷身份不匹配时失败关闭，不自动 `--replace`、删除或清库。空卷会执行首次初始化；纯 `podman run` 不能区分本次自动创建和预先存在的空命名卷，不将此描述为全部未知卷均可拒绝。历史数据库任意版本迁移不在承诺内；兼容性不满足时保留事实数据，由负责人先确认恢复方案。

## 1.4. 候选、发布与恢复

候选为私有完整 hash/来源/许可合同及单镜像归档，在独立发布 checkout 使用精确干净提交构建。未通过当次真实独立 Formal 不进入公开 GHCR。完整 `repository-baseline`、独立只读 review、receipt/hash DAG、许可核准及 GitHub 人工 Environment 审批全部保留。

发布先复核固定制品，再晋升原镜像、匿名确认 digest/config、创建 draft、上传三附件并逐项核对名称/大小/hash，最后公开 Release。镜像成功不是发行成功，GHCR 与 Release 不具备原子事务。部分成功或未知写入保留现场，先只读查明，不自动删除、盲目重传或覆盖同版本内容。具体流程见[正式发布](github-actions.md)。

## 1.5. 环境隔离

正式容器、开发调试、候选验收分别使用18080、18081、18082和不同名字/卷。发布仓库不接收未提交开发文件。验收资源有唯一标识，只清理经逐项确认归属的本次资源；旧安装、其他项目和原始历史证据保持不动。自动浏览器仅 Chrome for Testing，不操作日常 Chrome。
