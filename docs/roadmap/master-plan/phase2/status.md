# 1. 第二阶段执行状态

## 1.1. 授权与基线

- 用户授权完整设计后开发；运行形式为 macOS 本机调试与 Docker 服务发布，不制作桌面安装包、不新增公网服务或字幕外发授权。
- 子任务分支采用 `phase2-N-主题`，按任务范围本地提交并及时合回 `phase2`；未授权 push 或合回 `main`。
- 创建阶段分支前已获取远端并核对 `main == origin/main == 2648d18fe841b53872cf7fd27638b54e0f6592f0`，工作区干净。

## 1.2. 设计交付

- 完整设计包已在 `phase2-1-plan` 提交 `e0e91b6` 并 fast-forward 合入 `phase2`；包含功能分类、处理主线、日志、架构图、版本与 Docker 发布/使用合同。
- 设计包同输入 Change `abc4c459-26d2-45d8-bc0b-01318332a5ca`、Repository `1d38917d-0f82-4fd4-a8d5-f6d918e68e59`、validation `3cdcd3ce-3ee9-4e94-b328-60d53f522786`、review `826e1e89-76cf-48bb-9dbe-1cd397f588c2` 及 check `1b012659-2269-4e66-9468-e51c7791eedb` 均为 PASS，绑定 submission `5a395afc-5d10-46ba-afee-df422731b827`。
- 这些结果仅证明 QLT-2003 的设计包交付；不代替其他设计子任务 receipt、产品实现、真实观看或整个阶段验收。
- 图源经指定 skill 渲染并实际查看；独立 review 指出的 S5 右侧留白已定点修复并重验，失败记录全部保留。

## 1.3. 当前执行

- M芯片macOS用户指南已按用户授权改为源码自构建，不再等待预制验证包：宿主Java25/Node/npm构建、最小上下文生成ARM64 API/PG镜像、精确image ID绑定、公开ECDICT本机获取、Java导入、Chrome加载和PG loopback15432查看。CSV/ZIP不跟踪。独立本机验证8项测试、静态检查、Java/扩展构建、两镜像构建、隔离PG导入266285源行/265999条目/320100词形及API readiness200/UP、formal ready=true均通过，独占项目资源已清理；实际端口因15432占用改用随机loopback，原模板config仍验证。最新证据`validation-20261001T154908Z-local-build/continuation-idpin/continuation-summary/assessment.md`，350输入摘要一致；最初验证夹具Dockerfile命名错误保留，另用精确ID重跑避免tag证据差异。只读review发现OpenSpec新旧合同矛盾后，已整页统一并复核PASS。未实测首次ECDICT网络下载/工具容器、新机器Chrome/YouTube、完整资源/更新恢复或Formal；用户在新机器执行最终真实体验。

- 用户最新明确批准使用Podman、公开ECDICT导入临时隔离PostgreSQL，并允许清空本项目真实数据库旧表；本轮选择临时空间，未读取或删除真实库。原Docker-only环境前置不再用于阻止本机验证；Podman本机client6.1.1/server5.6.2、linux/arm64、podman-compose1.6.0已核实。正式发行适配仍须保留引擎身份与对象归属：原生info无Docker式ID，兼容API连续/info的ID会变化，不能假装直接兼容或忽略身份漂移。审计podman-runtime-audit-2026-10-01与Podman接入合同已留存。

- 公开ECDICT实际隔离发布已通过独立验证/复核：首次标准256MiB tmpfs因WAL写满失败并保留证据；新增显式dataset-import测试profile（3072MiB内存/2048MiB临时数据空间），普通测试仍512/256MiB、无持久卷、精确清理，未放宽正式发行2GiB预算。19项隔离服务测试与Ruff/Pylint/docs/planning/policy通过。新隔离PG中Java建表/发布成功，SQL复核version1、source rows266285、entries265999、forms320100、紧凑CSV摘要0b89ddba891bcaa7ef8f45cc67a7c835edeaf0ee0c07296de53c6f6d365f4a5f一致；lease已完整删除，9项相关冻结输入无漂移（不是全仓冻结）。证据isolated-publish-20261001T134413Z-49616/assessment.json，review isolated-publish-review-d0b3d0b9-0a39-467c-9b2d-4d8dbe62306e为切片PASS。未export或伪造分发授权、未真实库重建、未stage/commit，后续仍需资料导出/安装链、Podman完整旅程和正式验收。

- QLT-2002先行资源计算机制已落在ignored阶段工具resource_metrics.py及直接测试；统一CPU/时钟纳秒、两服务含cache内存合计，拒绝缺样/短窗/身份漂移/计数重置；时延nearest-rank p95保留全部失败分母，任一失败不可达标。独立验证6项零跳过且输入摘要稳定，证据resource-metrics-validation-20261001；只读review resource-metrics-review-5f437d19-1e29-43e1-9c23-bfd74e277eca为机制PASS。该工具只计算规范化观测，未接入真实Docker采集/生命周期，不作为公开入口或Formal输入，QLT-2002仍未完成；环境与公开ECDICT隔离导入授权仍待回复。

- QLT-2002资源执行入口尚未实现（Catalog为DRAFT，拟议verify.mjs不存在），不将连续浏览器测试冒称资源验收。已新增release-resource-acceptance-contract设计草案，明确原始含cache内存/CPU计数、失败样本、采样缺口与精确归属边界；父核正CPU单位和固定观测窗口585秒（冷启30秒是上限，不是额外必等下界）。已从CaptionHintController核实既有caption-hints与Server-Timing边界，选择更保守的完整loopback HTTP计时、375个变化零失败判据；不新增性能API。下一步仍需冻结采集器与Python生命周期的真实接线及宿主硬件证据，不能假设Node工具可直接接收Python内部session；草案本身未验收或执行。当前只读复核docker compose仍缺失、默认socket指向Podman；已请求真实Docker环境准备与公开ECDICT临时库导入授权，未自行安装或操作真实库。

- 连续合成使用切片完成375秒实跑与独立复核：最新运行continuous-validation-rerun-20261001T1057Z.947JCj持续375002.689ms，375次字幕变化，本窗口281次提示、94次无提示，网络/超时/协议/后端/拒绝/迟到/取消错误均0；124项单测、browser/API、docs/planning通过，冻结摘要一致，自有进程与profile清理完成。独立review continuous-review-aebffec2-89ab-478a-9133-0fde75c628a6为切片PASS。首轮报告把前置16次提示混入分子（297/375），review判FAIL；已修复为保存baselineCounts/windowCounts并按增量统计，补直接回归后重新完整375秒验证，旧报告不改写。该比例仅描述合成样例，不是词库覆盖率；未证明标准硬件Docker CPU/内存/后端p95、真实YouTube、完整Verify/Formal。

- EXT-2004 合成浏览器隔离修复已独立验证并只读复核：启动器直接使用既有Java25/Gradle，不加载本机私有配置；仅白名单工具环境、固定demo与关闭敏感记录；所有截图/报告写本轮独占目录，继承真实视频URL则拒绝。增量与多行夹具只统计/暂停字幕POST，状态GET透传，未放宽产品协议或既有断言。extension单测122项零失败/跳过，quality结构化PASS且browser/API各1，multiline 5例、underline 7例、experience 7项通过；docs/planning通过，422项输入前后无漂移、无测试进程残留。证据e2e-isolation-validation-20261001T102940Z，独立review e2e-isolation-review-0b28b3bb-746a-41f9-9496-f5108d9bce3f。此前越界入口、预检BLOCKED和多行FAIL记录保留；本结果不替代真实视频、Docker、完整Verify/Formal，未stage/commit。

- 开发集成回归已取得后端347项测试零失败/跳过、扩展120项单测通过，隔离PG/Redis清理已核实；358项输入前后无漂移。证据integrated-20261001T180301。整体结果FAIL：quality:check退出零但结构化报告browser-api-smoke-assertion-failed，增量字幕用例超时。该入口实际间接执行e2e并调用会加载本机配置的java_exec，违反此次禁止该调用链的边界；未重复运行，不以退出零或部分成功掩盖失败。已修正回归合同，另冻结EXT-2004浏览器启动隔离及GET/POST拦截夹具修复范围，待独立重验；未读取配置内容核查，也不据此宣称数据泄露或真实库改动。

- 精简词库真实Java只读准备已实跑：lexiconValidate、lexiconBasicReport、lexiconPrewarmReport成功，266285源行转换为265999条目，286派生行归并，无释义/非法词面剔除均0，来源基础词集合3344、预热报告2000条。输入为已校验的6.44MiB紧凑来源包，不连接或修改数据库；证据java-source-validation-20261001T101500Z，独立只读review java-source-validation-review-20261001T102000Z。冻结仅19项相关输入，不宣称全仓冻结；首个timeout工具缺失失败与后续Python有界执行成功均保留，完整launch命令/环境快照未单独持久化的限制明确披露。下一缺口是隔离空间实际发布/导出/安装链，而非再次压缩来源包；完整Verify/Formal仍未完成。

- Apple Silicon平台切片已完成独立确定性验证与只读复核：非空平台子集构建、候选与声明平台精确匹配；当期runtime仅实测M芯片macOS/ARM64显式端点；两代资料、恢复、停止与删除仍完整覆盖。Node35项、Python49项、Ruff/Pylint/docs/planning/policy通过，166项冻结前后无漂移；证据apple-silicon-validation-20261001T084920Z-48473、review apple-silicon-review-20261001T091500Z-review。长命令初次exec对象未保存，终态poll原始对象已保留，该限制不隐瞒。父只读探测宿主Darwin arm64、daemon报告linux/arm64，但docker compose version退出1，真实Docker/Compose生命周期仍BLOCKED；不将本切片当完整Verify/Formal。

- 词库紧凑格式已实现：7个有效字段与可逆单列来源映射；独立核验266285行词条/释义/字段及来源映射与上一已验证数据逐条一致。双构建ZIP均6756140 bytes（约6.44 MiB），SHA-256 11c1b3f6b2192b1b7fcfded2e13302c40ef87f1a9dbeb960aacfe3ca301e956e；CSV14999983 bytes、来源映射1826952 bytes。Python17项、Java定向19项零跳过、Ruff/Pylint/Java格式、docs/policy检查通过，证据在本机tmp/quality/phase2-ecdict-core/compact-validation-20261001T083603Z。OPS-2005为v5/change5；封版确认前ZIP、CSV等词库制品均忽略，源码/测试/来源锁可跟踪，未stage/commit。制品仅保存在ignored tmp，旧本机包移至tmp/phase-tools/prepare-phase2-pipeline/ecdict-analysis/ecdict-core-source-baseline.zip保留；不再放源码目录以避免Catalog归属冲突，移动后planning/docs补证PASS（compact-validation-followup-20261001T084022Z），独立只读review PASS（compact-review-7d77bd31-a421-4a99-a793-1244eff046d9），均只覆盖此次格式切片。来源包不等于Java发布安装包，完整Verify/Formal未完成。

- 当期支持范围以用户最新决定为准：仅 M 芯片 macOS 上的 Docker（linux/arm64 容器）；amd64 与其他宿主已进入后续待办，不再作为当期发布的必需环境。OPS-2008/QLT-2004 已升至 v3/change3，平台实现与检查接线已通过定向独立验证，尚未声称单平台真实验收通过。后文既有双平台测试/阻塞记录仅保留其当时事实，不再定义当期支持范围。
- ECDICT 保守规则已落地：339843个基础候选中保留265881、剔除73962（21.76%），50698个频率/词典标记词全部保护；补Oxford物理支持与3个既有curated来源后为266285行。不按字母序或配额截断。真实同源双构建ZIP均为7617625 bytes、SHA-256 bb450472c4782b2e59c63fe447dc479592ba4989209ea9072856cdd2539fc2d7；CSV为16597738 bytes，266285条来源映射逐条核验无误。15项直接测试与定向Ruff/Pylint通过，双构建和补测分别见本机run-f83851b2-bb79-4c44-bb99-dcfe72b0ea4d及test-coverage-supplement-4ae2eec0-c983-4aca-88b8-f1ba4b989f8f。预筛58590条0:词形中仍有6406条缺少保留原型的正向exchange覆盖，已逐条列入审计，不宣称全词形无损。独立只读审阅未发现本切片开放源码问题；该次压缩制品保留于ignored tmp中的ecdict-core-source-baseline.zip，未stage/commit。源码包尚非Java可安装发布资料；完整Verify/Formal与Java导入链未据此完成。
- ECDICT 已定位本机公开原仓库，origin skywind3000/ECDICT、commit bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b；原 stardict.csv 共3,402,564行，232,668,349 bytes，已核实同受控stardict.7z成员摘要一致。首轮候选200000单词+881标注短语，保留中文/词形/频率的gzip为5,051,396 bytes、CSV13,102,630 bytes；含英文definition时gzip8,217,587 bytes、CSV20,130,907 bytes。这是来源层体积实验，不是最终Java发布ZIP或数据库体积。已发现粗筛缺264条Oxford基础支持行与受信stream of data短语，冻结生成器合同要求补齐；OPS-2005升v4/change4，自动生成器/直接测试正在实现，未将原型数据加入Git或导入真实库。

- Gate 最小修复已提交 `6ad4269`，从 `phase2-2-gate-recovery` fast-forward 合回 `phase2`；全部失败记录仍保留，未推送远端。
- QLT-2005 同输入 Change `d3620a9e-edd4-4c71-a93b-42768a6fb74a`、Repository `d5a60b83-2865-48d9-970e-9c8db0aa15c2`、validation `cb8121b0-1c15-46c7-88ed-7c1b17a767f2`、review `d8e0a07b-0f35-4da0-a4bb-b8a77cacfc7a` 及 check `9a8e7b77-3520-405d-acbf-b648de28ac76` 均为 PASS；submission 为 `63d07782-1303-4bd1-8ad2-4f5f90b01849`。
- 真实 check 已成功消费 QLT-2003 唯一完整 PASS 链；旧失败记录没有删除或冒充成功，二期共 36 个 Task。
- 启动交接已提交 `6bf12df` 并合入 `phase2`；ARCH-2001 的 check 为 `3d73a8c4-476c-455a-b655-bad962b2631f`（PASS）。
- 已核对授权、一期缺口、原始主干起点、子任务 ownership 与本地集成事实；启动交接不改变业务规则或真实资料。
- LEX-2001 已提交 `e1b36db` 并从 `phase2-4-lexicon-classification` 合入 `phase2`；同输入 Change `2df4c253-5d46-418e-b1cf-5226eb02361c`、Repository `5273b27a-dde3-4c77-9453-07e592e6f4ec`、validation `0723fd3b-8dcc-4131-b237-45a5094f165d`、review `8727eb73-6449-4583-b065-c9555be8d15d` 与 check `d8c1a812-3968-4bb0-83ff-a8e8c9446c38` 均 PASS，submission `bda94e95-fdd3-4581-9f2f-5cbb8c8e5a62`。
- LEX-2002 已提交 `fb480fa` 并由 `phase2-5-phrase-quality` 合入 `phase2`；Change `f61bfb13-6edf-458f-b568-5cbaff6fb454`、Repository `f47fca4a-769f-44b3-b608-89f86c9ef04e`、validation `e2308a98-50d0-4da9-b5d9-b5ad4211cdcd`、review `5d0bf80d-50e6-43c3-8129-9255504438bb`、check `793ba163-cd3d-4597-82ad-a0e12bdcaee2` 均 PASS；submission `d1274345-ffef-4051-a3a9-3cdc846a238e`。
- LEX-2003 已提交 `c18c34a` 并由 `phase2-6-query-contract` 合入 `phase2`；Change `ac8d3b4a-a01d-49c2-a19f-e00e264e9dce`、Repository `f9b37a37-6072-44b1-9b13-7f6bc0b5d9b4`、validation `11ce59fb-462e-45f6-aba2-980a841a94c7`、review `b35b716a-0802-498c-b307-8179576543ed`、check `c7902434-a633-4b71-a529-7e5f44f5d4ef` 均 PASS；submission `40345063-a948-4dd9-99bd-f803338d6e66`。
- LEX-2004 v2 已提交 `d1d10fe` 并合入 `phase2`；Change `79c640a0-f686-4dc7-b85a-fef4b6a95975`、Repository `aeba64e3-e23a-45de-9483-f166715de82c`、validation `48738bdd-726c-4abb-b006-a9b1627380c9`、review `4bec142e-0d69-43d9-ae1b-5cf779a35e42`、check `b292a654-51b2-47eb-a0b2-654db66f37a3` 均为 PASS；旧失败送验全部保留。
- DAT-2001 v2 已提交 `b218fea` 并合入 `phase2`；Change `0d71c290-b826-4d0d-b91f-dc0c9b4a872a`、Repository `98d79e5f-2af8-4f14-9b88-1ac41847630e`、validation `970bcd9c-f6ba-4b83-8dbe-23c3d7f7f2c5`、review `c0e67ad3-113e-4241-8270-fe1e916c73c0`、check `70b3e175-9280-4c7f-b581-8a7af71dcfcf` 均为 PASS；仅隔离库执行 SQL，真实运行资料未改动。
- OPS-2001 v2 已提交 `9335aa5` 并合入 `phase2`；Change `8e50486d-cf0c-4225-b10d-24022e6872cd`、Repository `b34dc440-e8ac-4d92-a6a4-2d703fb33225`、validation `a22aa425-e62f-4760-89e9-0065b25a0e84`、review `f1922a5c-63d4-4eaa-a4aa-ad2e770743c6`、check `33c4ff85-b8c0-4250-abd8-cab55fd6ae0f` 均为 PASS。
- OPS-2002 v2 已提交 `5885803` 并合入 `phase2`；Change `3f95a374-4ba2-43da-b530-af3ea3a0f099`、Repository `039c50d2-12e6-48c3-9249-7049f87e627b`、validation `dc00c8ef-55bd-4852-aab4-279c73e78f62`、review `342ad910-3f4f-4d5c-ac71-e4740121aac5`、check `a204ec1b-f56d-497f-ab67-fb933c1cffb3` 均为 PASS；submission `ec7d868d-c1f1-4152-9807-6a7629399ccf`，旧失败证据保留。
- ENR-2001 v3 已提交 `4c9042b` 并合入 `phase2`；Change `bf3d1c54-a7eb-4189-a33a-e8aa66f4fd5d`、Repository `52ffa365-5691-4921-b95a-be33952a01bb`、validation `3054ae12-0e68-4ee4-9533-12134ddc46f2`、review `3ebf4815-d9c2-49e6-8c0e-2318694ea39c`、check `5726f525-e062-492b-8b0e-8b2bd4425f6b` 均为 PASS；submission `18f263cd-d5be-4e83-9f0d-91d3311fd722`。
- ENR-2002 v2 已提交 `584701d` 并合入 `phase2`；Change `f159e7ba-c494-472f-a78a-b1925b1d562c`、Repository `a1ff5301-6592-4437-b241-d9bb4d78929c`、validation `6bf40a87-a5b2-4ad2-8672-c68beec3fa16`、review `9bf6880e-b9ba-4502-a863-a3f1e1c4615a`、check `730245df-2789-4928-b80d-70128abba2f9` 均为 PASS；submission `5227ee41-3be1-41cb-bc45-4b829d78b2d0`。验证代理曾提前中断一次，保留该次日志；按既定超时等待后的完整验证通过。
- 补齐导入/缓存事件的实际接线任务 OBS-2003，并设为主线验收硬依赖；二期现有 37 个 Task，不把仅实现日志适配器当所有节点完成。
- OBS-2002 v2 已提交 `3ea85aa` 并合入 `phase2`；Change `72800900-1399-4d33-b325-881bc4641bb5`、Repository `854e5b45-6485-48c1-8b41-0eadbb0bd866`、validation `bccae570-fd57-464e-9982-892e76274552`、review `1659dff2-6566-4b81-84c6-3c6972336d2f`、check `b44ead86-799d-4b39-965d-15360f467581` 均 PASS，submission `1fb7ab07-5819-4aaf-9f3f-cf1c975ec082`；首次依赖锁缺项和后续修复证据均保留。
- API-2001 v2 已提交 `8e3eeb9` 并合入 `phase2`；Change `25a81148-5ed5-4232-9f40-0e92e9a80b27`、Repository `963a1b6f-a8ff-4f4d-af8f-e87585da2b53`、validation `a1f20940-02f3-47b5-9c82-a8a7ca21c2ed`、review `4dc0cd4e-b679-4461-9121-27f9f8a692df`、check `b8decd98-09ed-4eb3-86de-af5c3b76ea4b` 均 PASS，submission `ddacc66f-37b8-4299-b511-984a6edf2431`；首轮浏览器 smoke 隐式 demo 失败及后续显式模式验证证据保留。
- API-2002 v2 已提交 `b42892d` 并合入 `phase2`；Change `c95e7ace-fc5e-4ffb-ac1d-63f21d8aae31`、Repository `a5011303-f0ac-4155-8083-01356781abd2`、validation `5372ba59-6318-4e9d-a15d-28cff3d05575`、review `dae3a17e-29a2-481d-85aa-104101eff873`、check `be37d256-789f-4842-b276-2754047ff12d` 均 PASS，submission `20ead54b-e78f-4f46-b79c-71280c711936`。
- OBS-2003 已提交 `6a650fe` 并合入 `phase2`；Change `da0da359-067d-4f1a-a658-e3f2c6c121e6`、Repository `41e6dbed-69b3-404b-af1f-5e1db8269525`、validation `59ef0f43-1ed2-4fcb-a81e-21cde82b4f01`、review `aa18ce31-7205-4f97-8292-ffe6f2e45f49`、check `5e807d41-0e4c-4181-8952-563c092f5857` 均 PASS，submission `275eaa64-557b-446e-a90b-80ce30c5c107`；来源异常与 consumer 回调异常的审查失败记录保留，修复后重新送验；未推送。
- EXT-2001 已提交 `1475e07` 并合入 `phase2`；Change `aa6662d5-be06-432b-9fa3-13bb6a82b1ba`、Repository `7dc6cf78-ef7b-4c95-b2aa-a56f2259d3ea`、validation `7d9eab71-7378-49cc-bc78-34f38a0ba687`、review `087299ce-9b0f-4689-8148-9555043cf96a`、check `2ccd4040-0643-4529-b656-a86229b3e3ff` 均 PASS，submission `c583e527-2061-4e9f-8a8d-923f9e1805fb`；93 项单测与合成浏览器接线通过，不等于真实 YouTube 验收，未推送。
- EXT-2002 已提交 `e04b7aa` 并合入 `phase2`；Change `c5b680be-c844-4a31-b118-ee5d9ee95759`、Repository `b888c7fd-1b95-4734-87f8-d2ff69016125`、validation `a8b6aa2c-f7a5-4804-876b-7497a4fea38c`、review `39cfea34-f0a3-4ea5-a45a-dd10b6426d7d`、check `ae8f2a1c-8435-4038-8d5c-075a289a1cd5` 均 PASS，submission `92515f2e-65b0-44a6-9a84-ade66c24d658`；101 项单测及合成浏览器通过，另执行 10 秒消费者连续检查，不宣称 375 秒性能通过。首次验证因启动器未继承隔离环境 BLOCKED，历史保留，新送验完整通过；未推送。
- QLT-2001 v3 已提交 `019f637` 并由 `phase2-19-pipeline-acceptance` 合入 `phase2`；Change `4301b05f-d30f-46a6-a45f-4a6518816b12`、Repository `850bb797-77d0-4b0c-9f8d-af73c11611ca`、validation `e60fbfe4-17e3-4856-ab44-c989e93fa359`、review `873bb7ee-db06-468e-950d-1ccf56feb62e`、check `de1fa6dc-0e3d-4f1b-8c14-8dfd31e7793a` 均 PASS，submission `fb5b6af8-48dc-43b5-88f3-88b9cc25a65c`；真实合成发布/API、请求级正负缓存、换版与 CHECK 回滚、UTF-16 增量和复用矩阵通过。验证与 Hook 隔离资源清理分别为 `c642154fdcda41c697d342692f3fcedb`、`3188fc523d7d42a1a6543438a00d752b`；未修改真实运行库、未推送。
- OPS-2003 v2 已提交 `f74359e` 并由 `phase2-20-release-version` 合入 `phase2`；Change `dd1a809e-0a93-4a94-a231-3a6b9f064bf8`、Repository `c2d629fd-6162-4c81-8b04-f311a15f6971`、validation `d5e1f399-de13-4292-8fb5-dcd6852cd298`、review `81cda4a3-ad35-4735-8f21-34b85f6e4a03`、check `807c1370-abda-4b90-a3cc-3e219d08cec7` 均 PASS，submission `de9905f0-eb5f-48f1-a217-c4303fca1714`。Node 15 项、Harness 5 项、实际 JAR 与隔离干净发行构建通过；新脏文件能使配置缓存失效并拒绝。首轮路径/结果字段失败与修复记录保留，未推送。
- 当前在 `phase2-21-extension-package` 实施 EXT-2003 v2，扩展构建消费统一版本，打包仅含运行允许集的 ZIP，并让浏览器回归加载实际解包内容；不上传或声称商店发布。
- EXT-2003 实现工作包按已冻结合同、七文件有界模块与确定性直接测试分类，派发原生 `gpt-6-luna / low`；Goal 活跃且无受支持 Qoder 等待适配。父任务仅修改 Catalog、说明、Harness 输入接线及其回归，正式验证与审查由不同代理执行。
- API-2002 的回归使用独立测试 profile 和临时台账路径，按单文件单顶层类拆成三组 HTTP 测试并显式纳入 Catalog；早期测试曾因遗漏临时路径向默认本机分析台账写入合成样例，已修正测试配置，原台账未读取、删除或清理，失败证据保留。
- OpenSpec 与运行记录保留在 ignored 本机目录；共享交接位于路线图与 Catalog。

- 用户追加授权：环境发布验证受阻时先继续后续独立实现，必需验证保留 BLOCKED，不将跳过视为 PASS；A/B 未提交与历史报告保留。
- OPS-2004 v2 已实现严格发布清单、逐平台许可关联、流式校验与原子目录装配，并接入 Harness/Catalog。父任务完成集成小修复，直接测试 10 项及 Harness 接线测试 4 项通过；装配结束源码漂移、输出篡改及额外文件/空目录均拒绝。上述仅直接自测，不是独立 validation/review 或 Formal Gate PASS。
- OPS-2004 当前缺完整同输入 Change/Repository Verify、正式验收、真实许可及双架构运行证据，交付状态仍为 BLOCKED；环境项按用户授权暂缓。实现、失败与复验日志留在 ignored 运行目录，未提交、合并或推送。

### 1.3.1. 暂时完成与后续推进

按用户明确要求，以下三项在开发排期上标记为“暂时完成”，不再阻塞后续独立实现。此标记不是 Formal Gate 结果；正式验收仍为 BLOCKED，原失败证据、依赖合同和待补检查不删除、不改写为 PASS。

用户进一步要求暂不收尾这三项，并授权定位、终止属于本任务的遗留测试进程。当前会话已具备完全访问和执行模式，重新查询未发现匹配旧测试命令的运行，无需杀进程；此前受限查询不用于推断旧运行终态。日常功能研发与正式发行验收分开推进：使用既有定向诊断保留模块质量要求，不以打包、双架构或许可验收作为无关功能开发前置；完整 Verify/Formal 的发布要求不降级，实施缺陷不归入可跳过环境项。

| Task | 事项 | 开发排期 | 待补收尾 |
|---|---|---|---|
| EXT-2003 | 扩展版本与 ZIP 打包 | 暂时完成 | 最新完整验证、独立验收、本地提交集成 |
| QLT-2006 | 原生并发与 Qoder 单并发配置 | 暂时完成 | 环境受限回归、独立验收、本地提交集成 |
| OPS-2004 | 发布清单与制品目录装配 | 暂时完成 | 完整验证、独立验收、本地提交集成 |

后续八项按依赖推进：API-2003 就绪接口 → EXT-2004 扩展状态；OPS-2005 资料包与初始化 → OPS-2006 Docker/Compose → OPS-2007 更新恢复 → OPS-2008 当期ARM64构建 → ARCH-2007 使用指南；QLT-2004 随各项接入真实发布检查。API-2003 v3 与 EXT-2004 v2 已实现版本身份、默认关闭敏感记录及发送字幕前的协议检查；资料包由 OPS-2009 与 OPS-2005 分别承担 Java 导出校验和安全恢复，Docker 封装同步推进；环境发布验证可暂缓，不把未运行项标为通过。此处仅放宽实现排期，不解除正式发布的许可、依赖与验收条件。

以下按这八项归纳推进位置；“已有实现”不等于正式验收通过，详细证据与限制保留在下方。

| 事项 | 已闭合实现与机制（非整项正式完成） | 当前待补 |
| --- | --- | --- |
| API-2003 就绪接口 | 六字段状态、版本身份、敏感记录默认关闭；API/质量与实际 JAR 验证 | 同输入正式验收、真实容器 readiness 与发行联调 |
| EXT-2004 扩展状态 | 协议预检与 UI；合成浏览器、实际 JAR/API 切片及独立复审 | 真实 YouTube、Docker 部署联调与完整正式验收 |
| OPS-2005 资料包与初始化 | Java ZIP导出校验/安全初始化、隔离PG两代合成资料；紧凑ECDICT来源包、7字段读取与真实Java只读准备已验证 | 真实来源导出/安装链、Podman容器初始化与完整生命周期 |
| OPS-2006 Docker 封装 | 非 root API、私网 PG、Compose/health 合同及合成检查 | 真 Docker 容器权限、端口、重启与资源预算 |
| OPS-2007 更新恢复 | 状态/锁、更新恢复、环境与引擎身份、精确归属；首次根目录异常安全拒绝与诊断已定向闭合 | 真实daemon取消、跨入口身份与两代故障生命周期 |
| OPS-2008 当期ARM64构建 | 通用平台子集构建/精确候选装配已独立验证，保留双平台能力 | M芯片macOS原生ARM64实际构建启动、可信交接、受控CI与正式发行 |
| QLT-2004 发布检查 | 原始快照bridge、两个runtime scope；M宿主/单ARM64端点与两代合成生命周期已定向验证 | 显式ARM64 Docker/Compose端点、完整runtime执行与当前输入Verify/Formal |
| ARCH-2007 使用指南 | README 安装主线、生命周期指南与 macOS 开发入口 | 真环境全用户旅程、独立验收与前置正式依赖 |

当前 session 已启用完全访问并恢复执行模式；父任务重新查询本机进程，未发现匹配旧生命周期、资源归属和构建检查命令的运行，无需杀进程。解除这些读取域的开发隔离，但不补造旧运行终态或将旧结果改成 PASS。OPS-2007 的环境隔离、跨发行委派身份及中断保留锁已完成定向验证和独立技术复核；OPS-2008 候选负例已完成 29 项验证及技术复核。QLT-2004 的原始来源快照消费、动态候选摘要核对和 runtime 接线已实现，本机真实构件链已有独立证据；受控 CI、双原生 Docker 运行、真实 daemon 取消与跨入口身份的实际环境证据仍待补齐。仅在自有临时环境开发和测试，不操作真实安装；环境发布验证暂缓不等于跳过已知实现缺陷。

- OPS-2011/OPS-2007 已将隔离副本的两路径环境补丁原样集成到工作区，集成前摘要完全匹配基线。独立定向验证完成环境隔离 6 项与资源归属 13 项，零失败/跳过，后者 session `65310` 确认 exit 0；源码与冻结合同前后摘要一致。不同代理的只读技术复核通过，未重跑测试。证据在 `tmp/quality/phase2-docker-environment/integrated-validation/`，进程查询与集成原始返回在同级 `resume-preflight.raw.json`、`integration.raw.json`。这些合成 CLI 证据不替代真实 Docker、完整生命周期或 Formal Gate；未提交或发布。

- OPS-2008 已补计划摘要跨平台一致/JAR 变化不一致、候选原始摘要篡改、重复平台、不同计划及末次架构漂移的负例，并修正初始错架构的无效副作用断言。首轮 29 项中 8 项失败，父任务定位为新增 fake Docker 模板的换行转义及模拟架构切换缺陷，只修测试、不放宽产品校验。独立复验 `node ops/release/build-check.mjs` 为 29 项通过、零失败/错误/跳过，26 个冻结文件前后摘要一致；不同代理只读技术复核通过。首失败、修复前快照、原始工具返回和成功汇总分别保存在 `tmp/quality/phase2-build/native-negatives/`；成功入口只输出 JSON 汇总，未声称取得内部逐项 TAP 原文。受控 CI、真实两平台启动、跨主机可信交接及 Formal Gate 仍待补。

- QLT-2004 本轮补齐 JAR/SQL 与扩展源码的静态绑定：两个 runtime scope 各新增 41 个生产者声明输入及对应触发路径。9 项发布声明回归通过，覆盖逐输入变化及目录成员新增/删除引起指纹改变；修复前缺项失败和修复后原始记录保留在 `tmp/quality/phase2-release-validation/artifact-inputs/`。仅执行 Python 合成目录测试，不运行 Node/Docker；这不是完整实际读取闭包、候选动态摘要或 Formal 验收，完整 sourceSnapshot 仍需冻结独立实施范围。

- QLT-2004 v2 已补共享 Verify 源码快照安全边界：目录描述符逐层拒绝文件/祖先链接、特殊文件和私有入口；流式读取以初始字节数为界，文件与目录替换、成员变化均失败关闭；文件描述绑定字节数及执行位，缺失输入不能冻结为 PASS，报告消费核对完整快照而非只看指纹。父任务先用合成树复现链接被读取、执行位变化不改指纹；Luna 初稿仍有根失败异常逃逸、增长文件无界读取及目录身份重核缺口，按具体风险升级接手，旧源码和全部失败记录保留。首轮 pytest 入口缺模块未执行测试，随后 unittest 执行记录已恢复；对应 session 59188/93684 均有真实 exit 0，不新增未知运行。
- 父任务使用仓库 venv 串行完成 Verification 266 项、Delivery Gate 消费者回归 56 项，均零失败/跳过；scripts 完整 Ruff/Pylint 三项检查通过。首次定向 Ruff 因缓存路径落在不可写目录而失败，使用标准无缓存方式后复验；未安装依赖或放宽文件权限。原始 exec/poll、完整日志与来源摘要在 `tmp/quality/phase2-release-validation/source-snapshot/`。README 共享阅读合同已转为取得并校验发行产物、启动服务、安装扩展，保留 macOS 开发入口。上述是机制与消费者回归，不是完整 Change/Repository Verify 或独立正式验收；动态候选、Git 来源、真实生命周期、原生双平台及三组旧未知运行仍待补。
- 源码快照已完成独立只读技术复核及修复：复核发现显式生成物路径和畸形输入集合会被静默排除，父任务以两项实际失败复现后改为明确 missing；新增真实目录同名替换实验确认现有父目录 ctime/mtime/身份检查已拒绝该情形，未据此增加对排除子树的读取。最新串行 Verification 269 项、Delivery Gate 56 项与三项 Python 质量检查通过，零跳过；session 93516 确认 exit 0。独立复审核对六项源码/合同、五项日志摘要后对本安全合同给出技术 PASS，未重跑测试，原 FAIL 报告保留；证据在 `source-snapshot/audit-fix/` 和 `source-snapshot/independent-audit-final.md`。本轮另在工作区只读冻结了 19 个声明检查的 667 个源码文件，未执行任何 Check 命令，不是完整 Verify，也不代替后续同输入重新冻结。
- 发布环境补充只读核查：当前 PATH 及三个标准插件位置未发现 Compose；`/var/run/docker.sock` 是指向 Podman machine socket 的链接。没有连接该端点、读取 Docker 凭据、修改用户配置或安装/启动引擎；不能将此路径当成已经可用的原生 Docker Engine/Compose 环境。实际双平台运行仍按用户授权暂缓，不以 Podman 或模拟架构冒充发布验收。

- OPS-2008 已接入每次只构建所选平台、构建前后核对 daemon 架构，以及按完整构建计划摘要配对两份候选的装配入口。可追溯直接复验为 24 项通过，但父任务复核发现固定摘要篡改、重复平台、计划变化和末端架构变化等负例尚未补齐；另有代理早期测试调用未保存终态，不能用后续通过记录代替。该工作包保持 BLOCKED，暂停原读取域重跑与改动，失败及原始调用证据保留于 ignored 运行目录；未运行真实 CI、镜像启动或签发 Formal Gate。

- 构建到运行验收的静态接线已补齐：两种 runtime scope 各增加 15 个遗漏输入及触发路径，构建计划、执行器、装配、CLI 与镜像模板变动不再只选中合成构建检查。6 项独立 Python 接线回归通过，未启动 Node、Docker 或终态未知套件；证据在 `tmp/quality/phase2-build/runtime-selection/`。首轮回归发现现有内核先处理非零退出，真实入口的 exit 3/JSON BLOCKED 会记录为 FAIL；测试保留这一实际边界，并同时核对缺 Docker 的 BLOCKED、正常退出的结构化 BLOCKED 及 exit 3 的 FAIL 均不被构建 PASS 掩盖。退出语义统一仍由 QLT-2004 闭合，未修改内核或把原失败改写为通过。

- API-2003 v3 已实现固定状态查询、构建版本资源及默认禁用敏感台账；39 项 API 测试、25 项本机启动器测试和 `:api:check` 直接检查通过。首次完整 API 检查发现异常隐私处理与借用标准输出的静态检查问题，已作最小修复并复验，原失败日志保留；未签发独立验收或 Formal Gate。父任务随后发现两处 PMD suppression 不符合全仓自定义 Gate，已改为无抑制的身份错误映射与测试框架输出捕获；该修订已随整合后的 44 项 API 测试和 `qualityFull` 通过串行复验；不是沿用此前哈希的通过结论。
- EXT-2004 v2 已实现服务状态显示、严格状态解析及发送字幕前的无正文协议预检；118 项 Node 测试通过，覆盖取消、协议不符零 POST 和状态失败不影响页面开关/本机偏好。串行 quality 联调已尝试，118 项单测通过后 Chromium 启动受宿主权限阻断，浏览器用例未执行；工具原始结果为 FAIL，保留原日志，排期按环境 BLOCKED 暂缓，不把单测通过视为正式发布。

- 扩展请求边界已进一步收紧：状态 GET 与字幕 POST 均显式禁止重定向、浏览器凭据和 Referer。重新构建后 120 项单测通过、零跳过；其中新增原生 Node fetch 加自有随机端口 loopback 服务，实际验证 307/308 不抵达重定向目标，以及状态预检拒绝时字幕零 POST。首次 npm 启动因两个配置入口同时指向 `/dev/null` 而拒绝，尚未执行测试；改为本轮独有的两个空配置后完整复验通过，失败记录保留在 `tmp/quality/phase2-extension-status/request-boundary/`。没有连接真实 API 或视频，未重跑浏览器环境项，不代表 Chrome 实际发布或 Formal 验收。

- 资料包工作按实际职责拆分为 OPS-2009 v1 导出/离线校验与 OPS-2005 v3 安全恢复/CLI，各 90 分钟、同一顺序工作包；不缩减用户所需的资料包与初始化目标。首轮实现报告 BLOCKED 后已停止，父任务核对出整条目缓冲、审批与行校验未闭合、CLI 缺失失败退出码等具体风险，保留首轮五个源码快照及原记录，按具体风险升级接手后完成实现与直接验证。最终原生工作包 `3cd08d87-3cab-4f48-867f-8943a5d5156f` 的 completion、源码与日志哈希已由父任务核对，公开核对入口通过；这是实现结果，不是独立 Formal Gate。

- 资料包最终两条 Catalog 命令分别串行使用 Harness 隔离 PostgreSQL，均实际执行 35 项数据库测试且无跳过；Java `qualityFull` 通过。覆盖同一 JAR 的导出→校验→初始化、损坏包/同版本异内容拒绝、事务回滚、并发初始化、已有写入者隔离，以及跨独立 JVM 时区的 ZIP 字节一致性。跨时区审计曾发现 ZIP 时间戳差异并产生一份旧 completion，已保留但不用于新输入。未读取真实资料或赋予再分发许可。

- OPS-2006 v3 已实现预构建 JRE、非 root API、私网 PostgreSQL、同镜像初始化与短命健康探针的封装合同；静态封装和 shell/Java 直接测试可独立于容器发布环境推进，真实 Compose 生命周期验收仍须补齐。6 项封装合同测试通过，健康 Java 测试随 API 44 项和质量检查通过；父任务实际验证 bootJar 的 PropertiesLauncher 确实调用健康类、额外参数非零退出且无异常输出。bootstrap 已内置到 PostgreSQL 镜像，避免依赖发布清单之外的宿主脚本；入口权限由 Dockerfile 固定。父任务已注册封装合同 Check，4 项选择/冻结/失败关闭回归通过；首次测试错误 mock 了通用工具探测而非 Python 专用入口，已修正且保留原失败。当前 `docker compose version` 实际返回缺少 Compose 子命令，未安装或绕过运行环境。

- 更新恢复先由 OPS-2010 提供无宿主语言依赖的发行入口与离线匹配校验，再由 OPS-2007 承担有状态切换。入口作为清单中的独立制品绑定，不增加产品进程或要求用户安装 Node。首轮入口实现实际测试 2 项通过、1 项失败，父任务发现制品核验遗漏、输出覆盖与清单字节校验未闭合，已保留失败及源码快照并按具体风险升级接手；这些是实现缺陷，不归为环境问题。
- OPS-2010 的生成、强制文件核验、自体摘要槽、字节级清单匹配和原子独占输出已实现。父任务继续发现并补齐缺少 artifactRoot 时的核验旁路、文件错误路径泄漏和 shell 摘要管道吞退出码问题。最新组合直接测试 23 项全部通过：真实 clean Git 合成 fixture 经目录装配后实际执行 shell，覆盖篡改、完整性、幂等、竞态、符号链接、错误身份和失败工具。父任务核对最新五份源码与日志摘要，并分别从标准入口复验 12 项清单测试、11 项入口测试通过；12 项相关 Harness 接线回归通过。证据在 `tmp/quality/phase2-lifecycle/`，最终实现日志为 `sol-attempt-8.log`；原失败日志和此前摘要全部保留。
- 上述仅是离线入口的实现和直接测试，不是完整更新状态机或真实 Docker 启动。OPS-2007 尚需切换 journal、旧安装恢复、停止/精确删除授权及真实生命周期验收。完整同输入 Verify、独立验证/审查和正式 Gate 仍为 BLOCKED；单 Task 原生 completion schema 与 policy 的最小任务数不一致也未绕过或伪造第二任务。清单消费已变化，旧输入的通过证据不能直接用于本批。

- OPS-2011/OPS-2007 的安装状态、锁与更新入口已有部分实现，尚不能交付。最后一次直接生命周期测试丢失进程 handle，代理无法确认终态；保留在途状态未知，不重复启动、不把文本完成回调当作测试结束。父任务另发现资源删除先于归属核对、保留标签手工声明及凭据跨 UID 权限问题，均作为实现缺口保留，不归因于环境。
- Compose 的保留标签与凭据接入已定点修正：移除手工 `com.docker.compose.project`，仅保留两个自定义归属标签；宿主私有密码经独立子 shell 校验后作为 Compose environment secret，值不进入 argv 或父 shell，拒绝无效长度/字符、符号链接和失效锁。Compose 身份显式覆盖为安装/发行身份，镜像配置使用已选定的不可变发行 image ID，不依赖只在导入进程中存在的临时变量。8 项封装直接测试、4 项 Harness 接线回归及 18 项构建/候选装配检查通过，原始证据位于 `tmp/quality/phase2-compose-secret/`。这是 Main 的有界修复，不重派未知生命周期运行；资源归属删除顺序、有界 Docker 命令和完整更新状态机仍待修复，真实 Linux secret 权限与完整生命周期仍为 BLOCKED。
- 卸载资源删除函数已移除先 `down --remove-orphans` 后查标签的路径，改为完整发现/归属预检、逐项复核并精确删除、最终空集合确认。9 项封装直接测试通过，新增场景覆盖晚发现的错误归属零删除、发现/inspect 失败、未知名称、坏 ID、预检后归属变化、删除后新资源出现、删除中断和空集合幂等；4 项 Harness 接线回归通过。记录在 `tmp/quality/phase2-resource-ownership/`。这是独立 CLI 替身验证，不是实际 Docker 删除或完整更新验收；Docker endpoint 隔离、命令超时、其他启停动作归属和状态机缺口仍待补，原未知测试未重跑。
- Docker 生命周期适配命令已统一经过本机端点选择与固定 `--host` 调用，拒绝远端/无效端点、忽略继承的内部端点缓存；真实子进程替身验证了 context 优先级、Unix socket 固定、后续环境覆盖不改变端点以及 host/context/TLS 不传入命令子进程。10 项封装直接测试及 4 项 Harness 接线回归通过，日志在 `tmp/quality/phase2-docker-endpoint/`。未运行真实 Docker；用户 Docker config 的代理/插件/凭据配置隔离、命令超时/输出上限、跨入口端点身份及完整状态机仍待补，不能宣称运行隔离整体完成。
- 真实生命周期检查的 BLOCKED 输出已改为标准 JSON；实际 shell 回归覆盖缺失、无效、符号链接和伪造 lease，均不执行 Docker、不泄漏路径。13 项 Harness 直接回归通过，证据为 `tmp/quality/phase2-update/harness-check-4.log`；这不是实际容器或完整生命周期通过。原实现快照、失败记录和未知运行事实仍保留。
- 安装状态基础函数已修复信号后继续执行及摘要管道吞错：HUP/INT/TERM 非零退出，退出时只释放 token 仍属于本次的锁；摘要工具失败或摘要非法均拒绝继续，工具原始错误不外泄。12 项封装直接测试通过，包括初始化/已有目录加锁两条路径、三类信号、锁归属变化、两类摘要工具失败及真实文件摘要；证据在 `tmp/quality/phase2-state-primitives/`。这不证明 Docker 子进程已取消或完整更新恢复已通过；原终态未知的生命周期测试未重跑，超时、配置隔离与状态机其余缺口仍待补。
- 状态与发行记录已补严格字段/字节解析、路径祖先检查、先校验临时文件再原子替换、记录身份不覆盖及缺失秘密的同记录重试。首轮实现直接测试失败且出现覆盖后校验风险，已停止并保留快照，再按具体证据升级接手。父任务整合时修正跨平台 `stat` 失败输出污染、悬空状态符号链接、写入失败清理，并纠正一个会把合法状态误判为测试成功的拒绝断言。最新 24 项封装直接测试通过，日志在 `tmp/quality/phase2-state-records/parent-contract-1.log`；失败日志未删，不视为完整生命周期验收。
- 重启适配已改为 PostgreSQL 独立启动并健康后再独立启动 API，两次均使用 `--no-deps`，不重跑 initialize；导入镜像后同时核验完整 image ID、Linux 系统与目标架构。对应两项合成回归随上述 24 项通过；未运行实际 Docker。停止后重新激活、失败返回值、删除中断恢复、命令有界执行和配置隔离仍待继续闭合，真实发布与完整 Formal Gate 仍为 BLOCKED。
- 用户流程已补 `starting`/`stopping` 日志、停止后重新激活、失败切换的非零退出和候选停止失败时禁止启动旧服务；内部委派绑定入口自身 key，修改操作获锁后重读状态。删除采用资源清理成功后原子转移记录到固定 tombstone、提交指针、最后清理秘密和记录的顺序，支持各断点同参数重试；明确解锁拒绝链接到外部目录及未知隐藏条目。首轮失败与各轮日志保留，父任务补齐文件工具原始错误抑制和不稳定阶段禁止暂存清理；36 项封装/状态/流程直接测试通过，其中 12 项新流程测试实际执行两份 Shell 入口和文件故障注入，证据为 `tmp/quality/phase2-flow-recovery/parent-contract-1.log`。
- 上述流程测试使用合成运行端口，不执行真实 Docker，也未运行原终态未知的发行入口集成测试。首次目录创建中断收尾、命令超时/输出上限、Docker 配置隔离、全部资源操作归属与跨入口 daemon 身份仍待闭合；便携 Shell 的目录 rename 不是抵御同一用户恶意并发创建的原子 no-clobber 原语。实际发行、双架构、资源与正式独立验收仍为 BLOCKED，不能把流程直接测试视为完整 OPS-2007 PASS。
- OPS-2008 已补候选构建与正式发行的接线边界：固定基础镜像摘要与离线 image ID 分开记录，两个架构分别取证，候选产物不冒充已验收发行；待对接真实 lease/receipt 后实施，不另建平行 Gate。下一步继续修复 OPS-2007，并推进独立的双架构输入校验、QLT-2004 完整发布检查和 ARCH-2007 使用指南；真实容器、许可、资源与独立验收仍单独保留待补。
- OPS-2008 v2 已实现构建输入校验和固定 argv 生成：实际核对 clean Git、版本、JAR 摘要、已跟踪的固定模板以及两个平台的基础镜像身份；拒绝未知字段、路径逃逸、符号链接和命令注入。父任务修正了 FROM 参数的 registry digest 与配置 image ID 混用，并补齐模板排序、ignored 替身和完整 argv 断言。标准入口 `node ops/release/build-check.mjs` 由父任务复验 5 个顶层用例通过；构建与生命周期的 17 项 Harness 接线回归通过，Catalog、文档和投影检查通过。原始嵌套 TAP 导致的检查失败和 YAML 锚点冲突记录均保留，最终日志在 `tmp/quality/phase2-build/parent-check-2.log`。
- OPS-2008 v2 已接入候选镜像构建执行器：隔离构建目录和 Docker 配置、固定本机 endpoint、构建前核对全部基础镜像、构建后核对镜像身份与架构、导出四份镜像归档并记录摘要；候选记录只在全部成功后独占写入。父任务补齐后续导出篡改先前归档的拒绝检查和固定失败码。标准入口最新 11 项直接测试通过，构建与生命周期的 17 项 Harness 接线回归通过；日志为 `tmp/quality/phase2-build/parent-execution-check-1.log` 与 `parent-harness-execution-3.log`，源码与证据摘要另存 `parent-execution-final.json`。
- OPS-2008 v2 已接完整候选发行包装配：绑定两平台四个镜像记录与资料/扩展/SQL/Compose/许可描述，固定生成自包含入口，复用清单装配器生成 payload，原始输入和源码重验后才写候选标记；不执行安装或 Docker。父任务复核并补齐畸形输入固定错误、有界记录读取、摘要/记录/源码中途变化时撤销本次输出，纠正基础镜像与构建产物 image ID 混淆。标准 `node ops/release/build-check.mjs` 最新 18 项直接检查通过，4 项构建 Harness 接线回归通过；日志为 `tmp/quality/phase2-build/parent-candidate-check-2.log`，源码和证据摘要在 `parent-candidate-final.json`。首轮标准入口因父任务删除 fixture import 后遗漏调用而失败，原日志保留；删除无用的预生成步骤后复验通过。
- 构建执行测试使用真实子进程与合成 Docker CLI，候选装配测试使用合成镜像/ZIP/资料与许可说明，未执行真实镜像构建，不证明资料语义、许可获批或双架构发布。双平台实际启动、CI、真实许可与正式发行链仍待补；OPS-2007 未知测试亦未重跑或宣布结束。下一步接受控候选验收与使用指南，安装/更新恢复已知缺陷仍须修复；正式状态仍为 BLOCKED。

- Docker 命令执行器已加入普通 30 秒/固定初始化 600 秒预算，context 探测同样受控；两流写入时受文件大小上限约束，成功输出每流小于 256 KiB。原生首轮因无法闭合进程与输出边界返回 BLOCKED 且未改代码；按具体风险升级接手后，父任务又修正跨平台限制、timer 工具失败导致失去超时保护、快速退出取消 timer 的竞态，并保留全部日志。标准封装入口 45 项直接测试通过，包含 9 项独立真实子进程用例：输出边界、私有目录权限、普通/失败/超时/信号回收、计时器失败及超时后成功退出仍拒绝；日志为 `tmp/quality/phase2-docker-bounds/parent-contract-2.log`。
- 上述只证明执行器自身收到信号时的清理和直接子进程行为，不证明最外层安装入口取消已完整传播、Docker daemon 操作取消或 Compose plugin 后代退出。配置隔离、所有启停操作资源归属、跨入口 daemon 身份、清理工具自身失败和实际生命周期仍需继续验证；原终态未知的生命周期测试未重跑，完整 Task 与 Formal Gate 仍为 BLOCKED。
- ARCH-2007 v2 已补 README 的发行安装主线、Docker 生命周期指南和独立 macOS 编译/配置入口。父任务复核后修复了开发说明循环链接、删除发行目录早于安装资料的危险顺序、来源真实性核验缺失和连续命令失败后继续的问题；指南区分本地状态与在线就绪，保持首次 ROOT 不预建、恢复资料保留及删除确认。命令与源码对应表留在 `tmp/quality/phase2-user-guide/`；未执行文档示例、真实安装或独立验收，不解除 OPS-2008/QLT-2004 依赖。


- OPS-2011/OPS-2007 的 prepare、start、stop、health 和临时容器清理已接完整资源归属检查：任何变更前检查容器、网络、卷，固定初始化名称；健康和停止按完整容器 ID，停止不删除，临时清理只处理本发行 candidate/initialize。父任务补齐锁在发现期间丢失、创建后归属改变、停止后身份替换和首次空安装的拒绝/恢复回归；修复宿主 locale 下 `[a-f]` 意外接受大写或重音字符的问题，Docker 适配层改为显式 ASCII 字符集。此前失败保留，最终标准封装入口 58 项直接测试通过、零跳过，耗时 83.807 秒，未放宽 90 秒检查预算；证据为 `tmp/quality/phase2-operation-ownership/parent-contract-4.log`。
- 上述测试实际执行 Shell 与自有有状态 CLI 替身；本组为减少重复计时器开销只替换命令计时器，真实归属、Compose 参数和镜像导入检查仍执行，计时器另有独立测试。没有实际容器操作，原终态未知的生命周期测试未重跑。只读 Compose 版本探测退出 125，发布环境仍为 BLOCKED，继续暂缓；跨入口引擎身份、配置隔离、外层取消与首次创建中断收尾仍待补。状态基础函数同类 locale 校验风险另行保留待修，完整同输入 Verify、独立验收和 Formal Gate 均未宣称通过。

- 安装 state/token/random/digest 的身份校验已统一为严格小写 ASCII，新增真实 Shell 拒绝回归；秘密发布改为私有 pending 完整写入校验后再原子硬链接到最终路径，中断重试不改变已有合法密码。固定 pending 已接入精确删除及 tombstone 恢复，只读校验不产生或删除文件。首轮实现 7 项测试中 2 失败、1 错误，父任务发现过度替换校验函数和 Shell builtin 故障注入无效、记录绑定与删除恢复缺口，保留源码和失败后按具体风险升级接手；父任务再补文件大小工具失败不能被管道吞掉的回归。证据在 `tmp/quality/phase2-private-write/`，未运行实际 Docker 或原终态未知测试。
- 最新标准封装直接检查 72 项零失败/跳过；第一次 98.779 秒已超过原检查器 90 秒预算，未当作完整共享 Verify 通过。因新增状态/秘密恢复检查量与真实 record/lock 重验，两个封装检查器一致设为 120 秒，并在强制 120 秒子进程界限内重新执行，实际 94.212 秒通过（日志 `parent-contract-2.log`、计时 `parent-contract-2-timing.json`）。这只调整测试总预算，不改变产品普通 Docker 命令 30 秒、初始化 600 秒、冷启与任何资源门槛。首次 owner 目录中断、配置隔离、跨入口 daemon 身份和外层取消仍需补齐；真实发行与 Formal Gate 仍为 BLOCKED。

- OPS-2010 的发行入口摘要字符校验已补为严格 ASCII，不再依赖宿主 locale 的字符范围排序。先以实际装配的 clean Git 合成发行包复现错误路径，再修复并从标准入口复验 12 项全部通过；错误工具返回成功但带大写、非 ASCII 或冒号的摘要时，固定报 `LF_VERIFY_HASH_FAILED`、无原始输出。原来仍有完整摘要比对，本项不是已证明的制品摘要绕过。日志保留在 `tmp/quality/phase2-entry-ascii/`；首次安装根中断恢复正在单独确定合同，不把该小修当作安装恢复完成。

- 首次安装根中断恢复已完成一轮设计复核和本机文件系统原语实验，尚未实施：固定名硬链接可让合作初始化器互斥，`mv stage ROOT` 在目标目录存在时会嵌套，`mkdir` 则拒绝已有目标；但 mkdir 成功后、归属标记建立前的强制终止仍无法证明空目录来源。父任务拒绝首轮有矛盾的互斥与归属方案，也未将依赖人工静默拆锁的接手草案直接变成产品入口。保留安全阻断与未完成状态，不自动接管或删除未知目录；记录在 `tmp/quality/phase2-owner-init/`，不是完整恢复或双平台验收。继续推进配置隔离、引擎身份、取消链与实际发布接线等独立事项。

- 三项“暂时完成”的排期标记保持不变，后续八项继续处理。Docker 环境变量隔离已有未验收改动，父任务复核发现测试负例实际重复同一链接条件、Compose 新路径缺少端点初始化，以及继承导出属性可能扩大秘密传递范围；均作为实现/测试缺陷保留，不归入可跳过的环境发布验证。该批归属测试因代理未保存进程句柄而终态未知，已停止新增写入与重复测试；与此前未知的生命周期测试分别记录。复核与恢复要求在 `tmp/quality/phase2-docker-environment/parent-review.md`，未据此声明隔离完成或正式通过。

- OPS-2008 增加构建机固定 CLI：`pipeline.mjs images|assemble /absolute/request.json`，复用构建与装配函数，限制请求大小、文件类型及符号链接，失败只返回固定 JSON；不执行上传、安装或平台认证。首轮代理 10 次失败的原始 exec/poll 对象全部保留；父任务定位并修正 macOS 临时路径未规范化、合成装配缺少跟踪模板/资料许可关联，以及入口重复 schema 导致的错误分类问题，未放宽产品校验。新入口 6 项真实子进程合成测试通过，标准构建检查合计 24 项零失败/跳过，4 项 Harness 选择与冻结回归通过；证据在 `tmp/quality/phase2-build/pipeline-parent-*.log`。本批所有测试句柄均有终态，其中首轮 `test5` 的原始对象直接含 exit_code=1，无需 poll；这不解除旧生命周期及 Docker 归属测试的未知状态，也不是实际镜像/双架构/独立验收证明。

- QLT-2004 已完成真实运行接线的只读审计：现有 Podman PG/Redis manager 不能作为 Docker 发布授权，运行入口仍无成功路径，Verify 普通环境白名单也不传用户 Docker 配置。父任务未批准直接实现尚未定义传输的 capability、额外资源标签与自动 sweep；先收敛候选来源/同输入绑定、运行授权作用域及与产品清理职责的边界，避免新增平行 Gate 或重复生命周期。审计与具体缺口在 `tmp/quality/phase2-runtime-lease/`；Catalog 仍为 DRAFT，未修改环境白名单、未执行容器或声称运行接线已完成。

- 发布验收设计已收敛为同进程测试运行器管理私有工作区与清理，不新增跨进程授权服务；初次发行不再以“必须先有旧正式发行”作为前置，开发期受控真实构建 fixture 与最终发行证据分别标记，两原生平台分别取证。源码来源映射与跨主机证据传递仍待冻结，未实施或签发运行验收。本轮核对来源闭包时发现两个 backend Check 未冻结实际 Gradle 启动脚本及 Wrapper 文件，已补齐三个文件；新增回归先复现 FAIL，修复后 7 项发布声明检查通过，证据在 `tmp/quality/phase2-runtime-lease/wrapper-freeze-*.log`。
- 独立 `env -i` 合成实验确认：原生 env 不含额外 CF 字段，当前 Python（含 `-S`）启动后出现该字段，且没有保留调用者的同名 poison 值。此证据将原环境测试的该项失败定位为测试观察层问题，不据此放宽产品白名单或宣称 Docker 隔离完成。原始结果在 `tmp/quality/phase2-docker-environment/macos-environment-probe.json`；旧两组未知测试仍未重跑或宣布终止。

- OPS-2008 已补本阶段 Java 源码副本工具，仅位于 ignored `tmp/phase-tools/prepare-phase2-pipeline/`，不新增公开入口或放宽 clean Git 要求。父任务修正首轮遗漏 lockfile、路径祖先链接未拒绝及 Git 环境继承问题；13 项直接机制测试零失败/跳过。实际当前源码的 265 个文件已复制到自有私有 Git 副本，并逐文件复核字节、摘要和执行位，来源记录明确 `formalReleaseEligible=false`，原仓库 HEAD/index 未修改。证据位于 `tmp/quality/phase2-build/java-fixture-parent/`；代理原始失败及所有 exec/poll 对象另行保留。此工具仅覆盖 Java 构建输入，不包括扩展/完整发行输入；副本的实际构建与 smoke 边界见下一条，不能把临时提交当正式发行源码。

- 上述私有副本已用 Temurin 25 与仓库 Wrapper 离线执行 `productBootJar -Prelease=true --no-daemon` 成功，产出实际 `api-0.1.0.jar`（25021690 字节），未安装依赖、连接数据库或运行 Docker。随后从同一 JAR 启动独立 loopback/随机端口 API：运行状态六字段及 no-store 正确、liveness 200、缺依赖 readiness 503、合成字幕请求 503 且无正文泄漏、默认敏感记录未落盘；资料管理与健康入口的非法参数也安全拒绝。JAR 内版本/SQL 与来源一致，265 项当前源码摘要再次匹配，副本保持 clean Git，API 进程经 TERM 确认退出（143）。首次探针因误判 JAR 内 META-INF 位置而失败，已保留原始失败并按实际布局修正；日志中的 FSEvents 警告未导致 Gradle 构建失败。证据为同目录 `current-source-jar-build-result.json`、`current-source-jar-smoke-result.json` 及清理记录。这只是实际 JAR 的无数据库 smoke，不是测试全套、资料初始化、容器/双架构资源或 Formal Gate；构建缓存使用情况保留在日志中。

- phase2-dataset 的独立验证代理已在同一 265 文件来源副本执行 `:adapters:test :api:test :adapters:postgresIntegrationTest`，仅由 Harness 创建临时 PostgreSQL，未继承外部 JDBC。实际 JAR 覆盖导出→校验→初始化、跨时区相同 ZIP、重复初始化、损坏包/外部对象拒绝、事务回滚和并发初始化；首轮 `:adapters:test` 命中构建缓存，未把缓存 XML 当本次通过。父任务据此批准只对该单测任务关闭缓存补验，未重复 PG 集成；合并后 141 个不同测试实际通过（62 adapters 单测、44 API 单测、35 PostgreSQL 集成），零 failure/error/skipped。265 项原仓库及副本摘要一致，副本 clean Git；临时 PG 清理回执 `da9c405407954ea48265f1cf81b59612` 确认已移除，两个运行句柄均有终态。证据和逐 Task 测试映射在 `tmp/quality/phase2-dataset/fixture-validation/`，新结论见 `cache-reexecution/combined-assessment.json`，旧缓存阻塞与 XML 全部保留。此为 OPS-2009/OPS-2005 的独立直接测试证据，尚无同输入完整 Verify/Formal Gate；真实资料许可、容器层与双架构验证仍待补。


- OPS-2011/OPS-2007 的环境缺陷已在独立私有副本形成精确两路径补丁，未应用原工作区。修复 Compose 端点初始化、继承 export 属性导致的秘密外泄，以及 bootstrap 接受无换行多余尾字节的问题；改用真实 Shell/native env 的合成观察，分别验证 host/context、七字段与两秘密传递、FD 关闭/stdin EOF、非法秘密与后续文件工具不接收密码。Luna 原始记录实际有三次失败，callback 漏列第三次及超预算偏差均保留；Sol 接手两轮后父任务又修复新秘密别名仍可能继承 export 属性、bootstrap 负例未绑定自有 CLI 的假通过风险。最新父任务 6 项定向测试通过、零跳过，8.294 秒，运行句柄 57314 确认 exit 0；执行器函数与产品预算未改，原文件和 baseline 摘要均未变化。证据及最终未应用补丁在 `tmp/quality/phase2-docker-environment/isolated-repair-indx85ss/parent-repair/`。Shell 替身自身生成环境记账字段的观察限制保留，不把它视为 native Docker 逐键环境证明；两组旧未知运行、原工作区集成、真实容器及完整 Verify/Formal Gate 仍待补。


- OPS-2008 的阶段源码副本已扩展到 Java、扩展及发行模板，共用安全复制与私有 Git 实现，不新增公开工具。父任务修复首轮遗漏 tests 排除、误复制 ignored 文件、必需入口不全与枚举前祖先链接检查，保留两轮代理失败；22 项直接机制测试全部通过、零跳过。实际当前工作区的 323 个源文件（1390869 字节）已逐文件绑定到同一私有副本 commit `290278a5e4ab7dc047a7bcdc6b5d5c05729fda3a`，provenance 明确 `formalReleaseEligible=false`；未改原仓库 HEAD/index，未把隔离环境修复补丁冒充已集成源码。
- 同一副本已离线实际构建 `productBootJar -Prelease=true`，并用私有 HOME/空 npm 配置、锁文件与现有离线缓存执行 `npm ci --ignore-scripts`、真实扩展打包入口；无依赖下载或真实服务操作。JAR 为 25021690 字节，扩展 ZIP 为 30908 字节，均版本 0.1.0；ZIP 恰有 12 个运行文件、仅 storage 权限及固定 loopback 18080，checksum/descriptor 与私有源码 commit 一致，JAR 内版本/SQL 一致。各步后原源码与副本 323 项摘要/执行位仍匹配，副本 clean Git，构建句柄 99952 确认 exit 0。证据在 `tmp/quality/phase2-build/release-source-fixture/parent/` 的 `artifact-check.json`、`build-raw.json` 与各步日志。本项是同源码实际构建证据，不是 QLT-2004 的完整 Verify 动态输入冻结、真实资料许可、镜像/双架构/用户生命周期或 Formal Gate；这些仍待补。


- 同源码实际 JAR 的合成资料持久产物链已推进：父任务运行 93105 在 Harness 临时 PostgreSQL 的本次独占 source/target schema 执行真实 export 两次且字节一致、verify、initialize 与重复 initialize，各 CLI exit 0；启动同 JAR 后 runtime-status 为六字段 formal/ready=true/OK/datasetVersion=1 且 no-store，liveness/readiness 均 200。合成资料源由测试三表 SQL 构造，不是生产导入；MIT/example.invalid 是明确的测试占位，绝非真实分发许可。父运行的正提示断言为 FAIL：沿用持久化 fixture 的全英文短释 safe 被既有 PublishedCandidateEligibility 的中文短释要求正确拒绝，实际返回 NO_HINT；后续应修正合成正例，不修改产品规则或把该轮改写 PASS。
- 父运行 API 已确认 TERM 退出 143、两个本次 schema 删除、PG lease `4e2127aa08d247e69347334dae2e8f89` cleanup_verified/removed 均 true；原源码及副本 323 项摘要/执行位复核一致，敏感日志 sentinel 未产生且普通日志不含合成字幕标记。原始 exec/poll、失败报告和部分实际HTTP证据在 `tmp/quality/phase2-dataset/synthetic-artifact/parent-run-d0cb528e52fe468781a10506b65ff713/` 与 `parent-exec-raw.json`。代理早期源门禁/seed/schema名称失败的部分报告曾被覆盖且初始exec未保存，代理重述的终态文件不当作原始工具证据；限制已单独披露于 `evidence-limitations.md`，两份旧lease实际cleanup亦由父核对。资料正提示闭环、真实许可、Docker/双平台及Formal仍未完成。


- 合成正提示样例已按既有中文短释合同定点修正，仅将测试 prepared_gloss/final_gloss 与预期改为“可靠”，产品源码及规则不变；上一轮英文占位失败原始记录与执行脚本保留。父任务单次直接复验 45857 exit 0，6.553 秒：同源码 JAR export 两份完全相同的 1845 字节五条目 ZIP、verify、空 schema initialize 与重复 initialize 均成功；真实API runtime-status为ready=true/OK/datasetVersion=1及no-store，两健康探针200，caption-hints实际返回“可靠”、UTF-16半开35..43与固定合成词条/义项身份。API终态143确认，schema精确删除，Harness lease `11c094b2418548bcaba7bf9102e013ff` cleanup_verified/removed均true；323项原源码和副本摘要/执行位、clean fixture commit再次核对，敏感sentinel与普通日志泄漏检查通过。实际导出包SHA-256为 `8655bdbfb4ad84b8907ec5515e4d7ff785458b8c9c0eb5e9c25e6ed39040a559`。
- 上述直接机制验证证据在 `tmp/quality/phase2-dataset/synthetic-artifact/parent-run-8280e751ae134279be84193312ae85bd/`，原始工具对象为同级 `positive-case-exec-raw.json`；PASS仅覆盖本次合成资料→同JAR管理命令→实际HTTP链。数据仍由合成SQL构造，许可仍是明示测试占位；未运行真实来源导入、浏览器、Docker/双架构或资源测试，不签发完整Verify/Task/Formal结果，正式交付仍待补。

- 环境白名单合成回归已完成夹具接线修复：独立验证两份定向测试共 23 项通过，完整 Docker adapter 78 项通过、零跳过；原失败记录保留。完整 adapter 耗时 132.369 秒，超过 Harness 的 120 秒预算，不能据此宣称标准检查或完整 Verify PASS。证据位于 `tmp/quality/phase2-lifecycle-regression/fixture-alignment/validation-retest-2/`。
- 随后的安装入口检查 3 项中 1 项失败：测试错误地在 stderr 寻找 BLOCKED，而入口在 stdout 输出结构化结果。仅修正测试为精确核对退出码、空 stderr 和完整 BLOCKED JSON，独立复验安装入口 3 项通过；产品真实环境入口仍因缺少受控 lease 返回 BLOCKED。接续的生命周期检查 2 项均在 fixture prepare 阶段返回 LF_OPERATION_FAILED，尚待定位；证据位于 `tmp/quality/phase2-lifecycle-regression/node-retest/`。不能将合成负例通过等同于真实发布运行验收。

- 生命周期两代夹具已改为共享有状态 Docker 替身，并发现真实产品跨发行委派缺陷：`_project` 在初始化自身发行身份前使用父入口导出的 key。现先校验自身清单，再保留原目标 key、锁与 record 检查；补充污染继承身份、错误 key/token 零 Docker 动作回归。独立验证 `lifecycle-check.mjs` 2 项（68.650 秒）、安装入口 3 项（2.037 秒）、flow 12 项（29.026 秒）全部通过、零跳过、前后冻结摘要一致。证据在 `tmp/quality/phase2-lifecycle-regression/delegated-identity-validation/`，早期 prepare/activate 失败仍保留；独立技术审阅 PASS，报告为 `tmp/quality/phase2-lifecycle-regression/delegated-identity-review.md`。这是合成生命周期定向验证，不是实际 Docker 或 Formal Gate PASS。

- 中断锁补强已实现：HUP/INT/TERM 禁止 EXIT 解锁并保留 journal；正常退出沿用 token 专属释放，SIGKILL 自然留锁，恢复仍需明确确认操作已结束后 unlock。独立定向验证 state records 15 项、flow 12 项、Node 生命周期 2 项全部通过、零跳过，源码前后摘要一致；生成的 Python 缓存变化单独记录。证据为 `tmp/quality/phase2-lifecycle-cancellation/validation/`，独立技术审阅 PASS，报告位于 `tmp/quality/phase2-lifecycle-cancellation/independent-review.md`；重复信号未单独动态投递，保护机制仅经静态复核。实现前文件快照遗漏已披露，不补造历史 diff；本结果不证明 Docker daemon 或任意插件后代已取消，也不是 Formal 验收。

## 1.4. 一期与后续边界

- 一期真实 YouTube 最终观看确认、最新交付范围的 Formal Gate 和清洗规则对应的运行资料版本仍待收尾，二期启动不代表一期已 close。
- 观测随处理节点验收；上下文选义、模型、长短语新算法和性能专项留在后续池。
- 未连接、重建或修改用户运行数据库；验证只用隔离 PostgreSQL/Redis，已完成批次均有精确清理记录。

## 1.5. 设计子任务验收交接

以下设计已在共享工件中完成，逐项取得独立 validation/review 与公开 check PASS；没有用设计包 receipt 代替子任务，也没有据此宣称产品功能完成。对应送验清单留在 ignored `tmp/quality/phase2-design-handoff/submissions.json`。

| Task | Check ID |
|---|---|
| ARCH-2002 | `9eb95548-a469-43c3-98b0-9f149c646276` |
| ARCH-2003 | `0fa22ffc-d8b0-4e7d-84e2-365127d99a3c` |
| OBS-2001 | `66cba731-7447-421c-b985-12f69bb45f24` |
| ARCH-2004 | `0eabd2e9-a1d9-4268-b585-27806f75146d` |
| ARCH-2006 | `a94da8d3-8a38-4a84-aed9-6699ae877be5` |

- 浏览器验收已重建匹配最新来源的 JAR 与扩展 ZIP：323 项源码及执行位逐项核对，独立产物后检通过；证据在 `tmp/quality/phase2-browser-acceptance/current-build/`。这些产物来自合成干净 Git 副本，`formalReleaseEligible: false`，不代表正式发行。运行器只读安全复核已完成，真实浏览器执行仍为 BLOCKED：固定端口 18080 被现存 Java API 占用，尚未停止该服务或启动验收运行器。此环境阻塞不影响其他无依赖研发；不以全权限运行替代资源归属确认。

- OPS-2011/OPS-2007 正在补安装级 Docker 引擎身份绑定：跨入口不得因 context/host 变化而操作另一 daemon；新增私有 engine 文件和操作前后身份核查，未签发验收。首轮独立验证发现 shell 范围匹配在当前 locale 下误收非 ASCII ID，已改为显式字符集合；随后修复环境测试把后置 ID 查询当成业务调用、覆盖原始 argv/环境证据的问题，并让测试读取实际 binding 而非替代产品校验。最新定向结果为状态 16 项与环境 6 项通过；资源归属套件在 120 秒上限退出 124，终止前有 2 项 ERROR，完整汇总未产生，流程与 Node 集成尚未运行，整体保持 FAIL/未完成。证据在 `tmp/quality/phase2-engine-identity/`，历史失败保留；未连接真实 Docker 或读取真实安装数据。Node 负例已改为私有控制文件驱动独立空/克隆 daemon 与委派中漂移，待独立验证，不以已编写代替通过。

- 引擎绑定修复后续独立验证：只读资源观察改为阶段前后查验，逐副作用和 journal 提交前仍核对身份；此前 12 秒超时的 cleanup 单例在 6.920 秒通过。状态 16 项、环境 6 项、资源归属 13 项、流程 12 项已分别通过，安装入口 3 项通过；证据在 `phase2-engine-identity/phase-validation-2/`、`phase-validation-3/`。完整 Node lifecycle 入口仍在固定 120 秒总上限超时，前两项的 TAP 成功不构成完整入口 PASS。后四项定向诊断发现一处测试预期错误：启动失败的恢复必须停止候选 PostgreSQL，不能要求 running 不变；修正后该单例通过，其他三项拒绝/漂移负例已有定向通过证据。标准入口超时仍保留 FAIL，尚无同输入完整 Verify/Formal 验收；不以分项诊断拼装完整通过。

- 引擎绑定的标准 Node 生命周期检查已通过：复用同一真实两代场景的合法准备状态，将原六组行为保留为五个具名断言阶段与完整两代闭环；没有跳过校验、伪造 journal 或放宽 120 秒内部时限。独立验证返回 `checks_run=1`、零失败/错误/跳过，真实 exec/poll 约 103.689 秒，31 项相关源码前后哈希一致，受管进程组无残留；证据在 `phase2-engine-identity/fixture-reuse-validation/`。这是一个顶层 TAP 场景覆盖六组行为，不声称六个独立测试或真实 Docker 通过。此前标准入口超时仍原样保留，最新通过也不替代完整 Verify/Formal Gate。
- 生命周期实际读取的 `fake-docker-daemon.mjs` 已补入安装/生命周期/真实运行三组六 scope 的触发与冻结输入，并在 OPS-2011 Catalog 范围登记；无命令/环境/预算修改。独立声明回归 16 项、planning 52 Task 和 policy projection 检查通过，无 drift；证据在 `phase2-engine-identity/fixture-input-validation/`。这些声明回归不运行真实 Docker。

- Adapter 定向回归：Docker 命令边界 9/9 通过；合同套件首次 14 项中 2 失败、1 错误，确认是旧夹具缺实际 engine loader/binding 与中断锁预期冲突。补齐真实 state 加载、0600 合成 binding 和外部 CLI ID 分支，保持资源归属/secret 负例，按既定合同验证信号保留原 token 后，独立复验 14/14、零失败/错误/跳过，16.051 秒，源码冻结一致且进程组无残留。证据分别在 `phase2-engine-identity/adapter-regression/`、`adapter-regression-retest-1/`；产品代码和预算未改，完整标准 adapter 入口仍需独立结果，不拼接分项 PASS。

- 完整标准 Docker adapter 本轮为 FAIL：固定 120 秒超时、exit 124，进程组清理已确认；因入口只在整套结束时输出累计测试日志，本次 stdout/stderr 为空，无法给出完整测试计数。证据 `phase2-engine-identity/adapter-standard-validation/`，21 项输入前后哈希一致。未把分套件 PASS 拼装为标准 PASS。正在仅优化合成 CLI 的恒定 ID 回答启动开销，真实产品身份检查、用例与预算不变，尚无复验结果；这不是沙箱或真实 Docker 环境故障。

- 引擎校验开销优化的定向证据：合成 ID 查询的 Shell 分支保留真实产品检查；随后将每次 engine 文件的 link count/mode/size 合并为一次 stat 读取（GNU/BSD），不缓存、不减少检查次数。独立状态测试 17/17（含新增元数据负例）、资源归属 13/13 通过，分别 5.515/78.324 秒，进程组无残留、冻结一致；证据 `phase2-engine-identity/engine-metadata-validation/`。此前完整 adapter 超时仍为 FAIL，未复验的 Node/完整入口不得沿用旧输入 PASS。
- 发布来源绑定已明确下一实施切片：阶段工具实际调用 Verify snapshot kernel，再建立私有 clean Git 源码映射；明确不是正式 Verify consumer、不是发行候选。已补 runtime baseline/change 的 `.gitignore` 与许可模板触发/冻结声明，正独立验证接线；bridge 实现和合成负例已派发，尚未验收。该工作不依赖真实 Docker runner，未删除运行验收占位或放宽正式发布门槛。

- 发布来源声明接线独立验证通过：lifecycle 声明测试 17 项（零失败/错误/跳过）、planning 52 Task、policy projection 无 drift；22 项相关文件冻结一致，所有自有进程组已退出。证据 `phase2-release-validation/source-input-validation/`。两 runtime scope 现各 80 项输入；阶段 bridge 的真实实现与测试仍进行中，不将接线 PASS 作为完整来源绑定或正式 Verify PASS。

- 标准 adapter 的实时诊断输出已实现，独立机制回归 6/6 通过（首次方法名计数错误证据保留）；stdout 仍仅在整套完成时给终态 JSON。最新完整入口仍在 120 秒超时，stderr 留下 45 条成功进度，最后处于资源归属的 prepare/start 用例，未有终态摘要；因此继续 FAIL，不以部分进度称通过。自有进程组已清理、35 项冻结输入一致；证据 `phase2-engine-identity/adapter-live-log-retest-1/`。该证据改善故障定位，不代表正式发布或完整 Verify 完成。

- 来源桥接机制复测 23/23 PASS（104.426 秒，零失败/错误/跳过），12 项输入前后冻结一致，自有进程组已退出；证据 `phase2-release-validation/source-bridge-validation-retest-1/`。首轮失败记录保留，已修正合成临时目录规范化与测试替身 env 透传；独立源码审查仍在进行，尤其核查输出发布时的并发不覆盖边界。此结果不代表真实 Verify consumer、正式发行或完整 Verify PASS。

- 来源桥接输出竞态已修复为 Darwin 原子 no-replace 发布；独立合成 suite 25/25 PASS（113.132 秒，零失败/错误/跳过），12 项输入当前复算一致，自有进程组已退出，证据 `phase2-release-validation/source-bridge-atomic-validation/`。其他平台未具备已验证原语时 BLOCKED，不降级普通 rename；独立源码复审待回传，不把本机机制结果当跨平台发布或 Formal PASS。

- 来源桥接原子发布独立源码复审 PASS（限 Darwin 修复），见 `phase2-release-validation/source-bridge-atomic-review.md`；与本机 25 项机制回归相符，但真实 Verify consumer 尚未接入。下一接线设计已定位 baseline/change 双 ID、有效 Check 配置及去重 correlation 的具体差异，正在修订；阶段工具提升为长期内部能力已请求用户明确批准，不影响无依赖诊断。

- 标准 adapter 超时的定向性能诊断取得实测：ownership 13 项通过、78.774 秒；27 次 case 共 1125 条业务 CLI 记录，prepare/start 正例共 27.583 秒。只记录合成命令分类，不读取环境/秘密；5 项冻结稳定、进程组退出。证据 `phase2-engine-identity/ownership-timing-diagnostic/`；该结果不代表完整 adapter 120 秒通过，尚未据此调整产品查验或预算。

- ownership 合成业务 CLI 启动追加 `-S`，保持标准库脚本、全部产品检查和断言不变。定向命令 exit 0、77.799 秒，但验证 wrapper 的复用日志被后续命令覆盖，完整首条证据缺失，不能记录该次完整 suite PASS；原始工具对象及缺失说明保留于 `phase2-engine-identity/ownership-no-site-validation/`。标准 adapter 仍在 120.007 秒超时，45 条完成进度不是终态 PASS；6 项冻结稳定、测试进程组已清理。未证明这一启动调整解决超时，后续 runner 必须每命令独立日志路径。

- `-S` 启动尝试未证明解决标准 adapter 超时，且首次定向完整日志缺失；父任务已精确撤回本次单行尝试及其专属合同小节，核对恢复到保存的原文件字节，无其他修改被回滚。失败和缺失证据全部保留。后续优化须针对实测成本，不保留未经证明有效的试探改动。

- Verify 父端快照传输切片完成：仅固定两个 lifecycle ID 与未来 Python consumer 精确命令激活 stdin envelope 和结果配对，其余 Check/旧注册不变。独立全 verification 287 项、Python quality 3 项、planning 52 Task、policy projection 无 drift 全部 PASS，56 输入当前哈希匹配、进程组已退出；证据 `phase2-release-validation/parent-transport-validation-retest-1/`。首次仅 kernel 格式检查失败记录保留，已确定性格式化后重验。源码复审进行中；consumer/桥接提升/真实发布仍未接通，不作完整 Verify 或 Formal PASS。

- 父端 transport 独立源码审查 PASS，见 `phase2-release-validation/parent-transport-review.md`，复算 56 项与最新 validation 一致。该意见只涵盖 IPC/配对/去重切片；Harness 仍为旧入口，consumer 与来源工具提升未完成，不声明整体验收。

- 父端IPC的直接消费者回归：delivery_gate 56 项、阶段source bridge 25 项全部 PASS，分别11.295/113.461秒，44项验证期冻结一致、两个自有进程组退出；证据 `phase2-release-validation/parent-transport-consumer-regression/`。随后为Docker只读预检补Catalog两路径，不能将上述包含Catalog的旧冻结当完整最新版Verify。

- 下一实施块为已规划的Docker只读预检：本机unix endpoint、socket/daemon身份、Compose和架构能力；已冻结独立合同并派发。只写内部库和合成测试，不读凭证、不操作真实资源、不改Check注册。剩余源码还包括runtime consumer/候选构建与同进程运行编排，旧lease占位代表实现未接通，不是等待外部签发lease；缺Compose/双平台/资料许可则是另行环境和证据缺口。

- Docker 只读预检首轮独立验证 FAIL：12 项中 10 失败、零错误/跳过，后续聚合检查未运行；66 项冻结输入稳定。证据 `phase2-release-validation/docker-preflight-validation/` 保留子进程回收告警及部分孙进程清理无法核实的边界，不能归因为沙箱。独立源码审阅另发现默认 context 解析、空 PATH 搜索及 cleanup-failed 分类问题，见 `phase2-release-validation/docker-preflight-review.md`。已派发有界修复；未触碰真实 Docker 或用户数据，尚无复验 PASS。

- 标准 adapter 耗时分解取得三组合成微基准：直接业务 fake CLI、真实有界 runner 包装同 CLI、真实身份检查各 30 次成功，分别耗时 1.403794/1.889750/1.385845 秒，均摊约 46.793/62.992/46.195 毫秒；源码冻结稳定、自有进程组退出。证据 `phase2-engine-identity/runner-cost-diagnostic/group-*-r2/`。首轮诊断因多余私有 socket 路径过长失败，父移除无消费方的 socket 创建后复测，原记录保留。微基准只能帮助定位重复启动与 runner 开销，不能直接推出整套优化收益或 adapter PASS；尚未据此改产品安全检查。

- Docker 只读预检机制验证已通过：定向 15 项、完整 verification 302 项、Python quality 3 项、planning 52 Task、policy projection 无 drift，66 项冻结输入当前复算一致，测试进程组和短期目录已清理；证据 `phase2-release-validation/docker-preflight-validation-r5/`。独立源码审阅 PASS，见 `docker-preflight-review-r2.md`（审阅时聚合命令尚未终态，父随后核对完整结果）。失败记录全部保留：r2 为 runner 临时路径过长；r3 暴露 Darwin zombie 组信号 EPERM；r4 为测试启动限时及 /tmp 规范路径预期。父原语诊断确认活组信号成功、zombie 组 reap 后消失，因此以直接子进程回收且最终组明确不存在为清理成功条件，未知仍失败。真实 Docker、runtime consumer、完整 Verify/Formal 尚未完成，不以该机制 PASS 代替发布验收。

- 浏览器状态切片完成独立执行：父依据用户明确授权，核实原 Java PID 50642 的仓库 cwd 与启动时间后仅发 TERM，正常退出并释放18080；证据 `phase2-browser-acceptance/owned-api-stop/`，未读取或修改真实数据。以最新323文件来源副本重新构建 JAR/ZIP，复核前后哈希与执行位一致。真实扩展原包与合成资料实际JAR正例6项浏览器/4项HTTP断言通过；协议不匹配负例7项通过，状态查询3次、字幕POST为0，变化后观察5秒英文仍可读。证据 `phase2-browser-acceptance/current-acceptance-r5/`，父已查看两张合成截图；API终态、两个专属schema、Harness lease、Chrome/临时目录和固定端口清理均已核对。独立源码/证据复审进行中；不代表真实YouTube、双平台、性能或Formal验收。
- 本轮浏览器前置失败均保留且已明确分层：`current-acceptance-r2` 为临时构建runner猜错API JAR文件名，r3 为外层临时HOME使Podman无法找到已运行的本机连接，r4 为浏览器脚本漏导入mkdtemp；均非沙箱阻塞。已分别修复精确API产物路径、仅Harness连接发现所需HOME、缺失import，JVM/Chrome仍使用本次私有目录。未安装或重启容器引擎。

- 浏览器状态切片独立证据复审 PASS，报告 `phase2-browser-acceptance/current-acceptance-r5/independent-review.md`，未发现本切片实质阻断。报告核对323文件及制品身份、13项浏览器断言、真实JAR/API正例、仅用于协议拒绝的负向stub以及归属清理证据；父已实看两张截图。该结果不签发完整Verify/Formal，也不覆盖真实YouTube或双平台性能验收。

- 正在验证Docker有界runner的字节统计合并：每命令只用一次wc读取两个固定流文件，仍分别保持256KiB成功阈值；预算、身份检查、捕获权限和精确清理均不减少。增加一次双文件调用与非法统计不泄漏两项测试，独立源码审阅未发现实质问题，直接/完整adapter结果待终态。证据 `phase2-engine-identity/batched-stream-count/`。该生命周期源码改变已使此前浏览器release-build的323文件整体来源绑定不再是最新全树，不覆盖历史浏览器证据，也不据此宣称本轮完整Verify已通过。

- 字节统计合并未解决标准adapter超时：11项定向测试通过，但完整入口120.074秒超时，47项已完成、下一项仍为prepare/start，未有suite终态；21项冻结稳定，自有进程组与临时目录精确清理。证据 `phase2-engine-identity/batched-stream-count/validation/`。父已精确撤回本次两份源码/测试及专属合同修改，字节核对恢复保存前镜像，保存attempted文件、失败记录和revert摘要；没有回滚他人改动。当前不保留未经证明解决问题、却增加解析复杂度的优化。

- ownership 测试替身优化已进入实现：仅改 `test_operation_ownership.py`，保留原 13 个测试方法、业务模型及产品 Shell 检查，使用每测试一个 Python 服务与私有普通文件传输，未调整标准 adapter 的 120 秒预算。合同为 `ownership-fixture-transport-contract.md`；原逐次 CLI 镜像与新旧逐调用等价夹具留在 ignored 目录。独立执行前的静态预检发现故障注入脚本存在断言失败时遗漏 client 进程组回收、无副作用断言无实际观测的问题，因此尚未启动任何业务验证；证据 `phase2-engine-identity/ownership-transport-validation/`，正在修复夹具，不宣称性能或测试 PASS。

- ownership 传输优化独立 r2 验证：新旧各13项测试、各27次调用逐字节/退出码/日志/状态完全相等；8项传输向量、6项故障场景及原13项直接测试均通过。完整标准adapter仍在120.011秒超时，46项完成、下一项为碰撞/锁丢失检查，未有终态；结果为FAIL。26项输入冻结一致，超时遗留的唯一自有fake-server按fixture PID及完整argv核实后精确终止，临时目录已清理；证据 `phase2-engine-identity/ownership-transport-validation-r2/`。父追加仅对已确认可读且为空的响应文件不启动cat，非空流仍原样输出、产品核身/预算不变；r3独立验证进行中，不把r2局部结果作为标准入口PASS。

- 完整逐用例耗时诊断取得终态：82项测试全部完成，0失败/错误/跳过，耗时156.792秒；22项冻结输入一致，自有进程组和临时目录清理完成。证据 `phase2-engine-identity/ownership-suite-timing-diagnostic/`。其中ownership65.508秒、lifecycle-flow34.949秒、bounds17.463秒、contract17.175秒，说明标准入口120秒不足以容纳本次完整串行套件，不能把诊断结果当标准PASS。已请求用户批准仅将测试入口超时改为240秒，产品资源、初始化、冷启及性能预算完全不变；批准前不改Harness时限，不再把重复相同超时当新验证。

- 等待测试时限决策期间已完成当前Node消费者回归：默认 `lifecycle-install-check` 3项、`lifecycle-check` 1个顶层复合用例、`build-check` 29项均PASS，分别2.053/106.506/15.728秒，零失败/错误/跳过，未放宽各入口120秒预算。51项冻结输入父复算一致，所有自有进程组和临时目录清理完成；证据 `phase2-engine-identity/current-node-consumer-validation/`。安装套件对缺lease返回BLOCKED的预期断言不代表真实发布已接通；仍不替代Docker标准adapter、完整Verify或Formal。

- 用户明确批准源码桥接提升为长期受测代码，以及Docker合成套件两个入口总时限120→240秒。已按精确Check调整声明和回归断言，产品命令、资源和性能预算完全不变；来源模块提升与独立验证继续推进，尚未声称标准套件或真实发布完成。

- 批准的Docker合成套件时限已独立验证：声明回归6项通过，标准adapter完整82项通过，149.702秒、零失败/错误/跳过，处于240秒上限内；30项冻结输入父复算一致，自有进程组和临时目录清理完成，证据 `phase2-engine-identity/approved-docker-budget-validation/`。这不是实际容器或正式发布验收，产品全部预算不变。
- 来源桥接已提升至 `scripts/verification/release_source_bridge.py` 并采用常规包导入测试。首次25/327测试通过但Ruff F841失败，记录保留；确定性去除未使用绑定并保留路径解析求值后，独立quality3项、完整verification327项（含bridge25）、planning52任务/12检查、policy无drift全部通过，112项输入父复算一致，证据 `phase2-release-validation/source-bridge-promotion-validation-r2/`。独立源码审阅0项实质发现，报告 `source-bridge-promotion-review.md`；仅完成长期化机制，provenance仍为非本轮Verify绑定、非正式发行，真正consumer/候选与生命周期接线继续待实现。

- 发布来源消费 API 已补齐：严格重建 baseline/change 两个有效 Check，消费父端同一快照并复用来源复制，不另冻快照；provenance 的运行绑定/正式发行资格仍为 false。首轮 331 项回归通过后，独立源码审查发现 Python bool/int 弱相等缺口，父加入递归精确类型比较及真实消费负例；r2 因测试漏导入 json 为 FAIL，原记录保留。补齐后 r3 独立 verification 332 项（含来源桥接 30 项）、Python quality 3 项、planning 52 Task/12 checks、policy projection 全 PASS；114 项输入父复算零差异，进程组和临时目录清理确认。证据在 `phase2-release-validation/source-consumer-validation-r3/`，独立复审 `source-consumer-review-r2.md` 未发现新实质问题。这仅完成内部来源消费能力，Harness 真实运行注册尚未切换，不代表真实 Docker、完整 Verify 或 Formal PASS。下一实际接线已定位固定基础镜像输入、合成已发布资料生产以及双原生架构候选汇合的代码/环境边界，不读取真实资料补缺。

- 发布运行验收的合成资料生产链已实现：integration 内部 Gradle 任务复用真实发布 CLI，再以实际 API JAR export/verify 产生两代资料包；不手工 seed 发布表，不读取真实词库，结果明确 synthetic/non-formal。独立 r7 生产任务、runtimeSmoke 实跑 8 项（含新 producer 6 项，零失败/错误/跳过）、Gradle check、planning 52 Task/12 checks、policy projection 均 PASS；check 中已有未变任务可为 UP-TO-DATE，不声称全体 Java 测试本轮重跑。290 项冻结输入父复算零差异，Harness lease、子进程组及专属临时输出已清理，证据 `phase2-release-validation/synthetic-producer-validation-r7/`；独立复审 `synthetic-producer-review-r2.md` 未发现新实质缺口。首轮 runner 的导入/日志类型故障、Jackson 3 API/Gradle 配置缓存故障、macOS 临时路径链接拒绝与 Javadoc 失败均保留；已按实际失败层修复，未归因于沙箱。为避免读取私人 runtime.json，本次明确使用精确 Java25 与直接 Gradle，在 Harness 测试服务上下文内运行，不将该定向验证称作完整 Verify/Formal。真实 Docker 候选、双原生架构及完整生命周期仍待接线与验收。

- 固定基础镜像已接入构建器与 Verify 来源闭包：双平台 registry manifest digest/config image ID 来自已核对公开元数据，descriptor 必须匹配 Git 锁文件；锁摘要进入构建 plan，禁止自动 pull。独立 r1 标准 build-check 30 项、Python quality 3 项、verification 332 项、planning/policy 均 PASS，零失败/错误/跳过；202 项冻结输入父复算零差异，证据 `phase2-release-validation/base-image-lock-validation-r1/`。独立合同复审仍进行中，特别核对锁文件专属负例覆盖；尚不宣称此切片完整验收，更不代表真实容器、双原生架构或 Formal 完成。原有 dirty Git 拒绝规则不变，未执行仓库 stage/commit。

- 基础镜像锁独立复审发现 5 类合同负例证据不足，已补锁 symlink、超限大小、未知平台、畸形 JSON 与构建中锁漂移；保留原软件版本漂移测试。追加验证发生证据目录碰撞：验证代理误复用 r1，覆盖其冻结文件及一条原始工具对象，虽原 stdout/stderr/status 部分仍在，r1 不再作为完整可复核证据链。不得据此签发验收；已要求停止写 r1、在独占 r2 重新冻结并实际执行全部命令，保留碰撞披露，不补造丢失证据。

- 基础镜像锁 r2 已在独占证据目录重跑：标准 build-check 32 项、quality 3 项、verification 332 项、planning/policy 全 PASS；219 项冻结输入父复算零差异，五组自有进程/临时目录清理完成。独立 `base-image-lock-review-r2.md` 关闭锁文件 5 类负例覆盖 finding，明确排除损坏的 r1 证据链；证据在 `phase2-release-validation/base-image-lock-validation-r2/`。该固定依赖切片的直接验证与窄复审完成，不是完整 Verify/Formal。下一消费者接线采用两个显式 Unix native daemon 串行构建/运行；容器自身 API 探针与宿主 18080 可达性分开证明，后者仍是完整验收要求，不被内部健康检查替代。

- 容器内运行状态探针已实现固定双端点、严格六字段 JSON 与共享 2 秒 deadline。独立 r1 在 Java25/Harness 隔离服务下 API 实跑 51 项（其中探针 10 项，含慢响应头/body/联合时限）通过；Gradle check 为 FAIL，具体是嵌套类型缺 Javadoc 与 PMD CloseResource，planning/policy 因首败未运行。345 项冻结一致、自有服务/进程清理确认，证据 `phase2-release-validation/container-runtime-probe-validation-r1-8cd24490abf449e0bd5cf395c02f3d95/`。继续修正资源生命周期封装及类型说明，不抑制门禁、不放宽 deadline；尚未称此探针验收通过。

- 容器内探针 r3 独立验证四命令全 PASS：Gradle check、API 实跑 51 项/探针 10 项（零失败/错误/跳过）、planning/policy。345 项冻结输入父复算零差异，Harness PG/Redis、自有进程组与临时目录清理确认；证据 `phase2-release-validation/container-runtime-probe-validation-r3-580063e9094540cc97ba988f560b716b/`。r2 的中文注释/record 文档与资源局部别名门禁失败均保留；最终使用私有 AutoCloseable transport 所有权及明确非阻塞取消，不抑制或修改质量规则。独立复审进行中；容器内 HTTP 语义探针仍不等于真实双架构执行或宿主端口可达性证据。

- 容器内探针 r3 独立复审完成，`container-runtime-probe-review-r3.md` 未发现实质合同偏差，确认源码/测试及四命令证据；后续 Catalog 变更不纳入此前全树输入结论。已冻结 `release-runtime-execution-contract.md` 并开始 QLT-2004 消费者实现，范围为固定 pipe 消费、真实构件/两代合成资料、显式双 Unix native daemon 编排与生命周期断言；两个 Harness 运行注册暂不切换，待真实代码路径及直接测试一起完成。宿主可达性、真实双平台环境、性能和完整 Formal 仍未完成。

- 发布消费者仍在实现中。父已将来源桥接本身、桥接测试、消费者三组测试和环境映射测试纳入两个 runtime Check 的冻结输入/触发，并同步 bridge 支持闭包与声明断言；原运行命令尚未切换。已核实产品生命周期按所选 daemon 的 Architecture 选择平台，不需要伪装宿主架构。要求消费者保留标准 checks_run/failures/errors/skipped 结果合同，实际阶段未运行不得计作成功；独立验证尚未开始。

- 消费者独立 r1 因中文 docstring 门禁失败；修正文档后 r2 quality 3 项 PASS，直接测试 28 项中 3 个 error，完整 verification/planning/policy 尚未运行。错误涉及声明读取返回类型、合成 producer fixture 的输出目录生命周期及生成入口提前清理；均留原证据 `runtime-consumer-validation-r1-53ce5c8e196844cd962173fa0348f8ff/`、`runtime-consumer-validation-r2-fcfdab6a796a422a81d2c8b60af0bb96/`。独立源码审阅另发现 PostgreSQL 健康断言缺失、bridge 失败清理吞错、子 CLI 独立进程组可能逃逸父 Verify 超时回收。前两项与测试错误已定向修复，进程组终止转发及真实合成子进程负例继续补齐；未切注册、未运行真实 Docker，也未称消费者验收通过。

- 消费者 r5 独立五命令全 PASS：Python quality 3 项、直接测试 32 项、完整 verification 354 项（均零失败/错误/跳过）、planning 52 Task/12 checks、policy 无 drift；468 项冻结输入父复算零差异，所有命令自有进程组/临时目录清理确认。证据 `phase2-release-validation/runtime-consumer-validation-r5-4e45a2d5c1f946709be545f4161e015b/`。r3/r4 的声明顺序、缺少自有 owner fixture、macOS /tmp 链接及输出路径失败已按实际接口修复，未弱化来源拒绝规则；此前失败全部保留。包含真实桥接、真实生成产品入口单代状态/删除结构和合成子进程 TERM/timeout/overflow/持管回收用例；双平台双代编排仍为 stub 边界测试，非实际 Docker/Java 双平台证据。修复后的独立复审进行中，两个正式 runtime 注册仍未切换。

- 消费者 r5 独立复审已完成（`phase2-release-validation/runtime-consumer-review-r5.md`）：bridge 清理吞错与 PostgreSQL 健康断言两项关闭；仍发现直接子进程被回收后使用旧数值 PGID 发送清理信号的复用竞态。未观察到实际误杀，但接线前必须修复，不能将登记过的 PID 永久当作归属凭据。已派发仅 consumer/直接测试两文件的进程生命周期修复，保留 r5 测试证据而不称整体验收通过；两个运行注册继续保持原入口，不操作未知进程。

- PGID 生命周期修复后的消费者 r6 独立 quality 3 项 PASS；直接测试 34 项为 FAIL（1 failure、3 errors、零跳过），完整 verification/planning/policy 未运行。实际 macOS 上对已退出但未回收子进程组发送信号返回 EPERM，并使外层 TERM 返回值及流关闭异常；不能据错误文本归因为沙箱。468 项冻结前后一致，证据 `phase2-release-validation/runtime-consumer-validation-r6-42cb4b8c80c94d3a9bc4978222f11015/` 保留。父决定在原 QLT-2004 身份内针对已证实的平台语义/清理归属风险升级一次 Sol medium 修复，范围仍仅消费者与直接测试，禁止吞掉 EPERM 冒称安全清理；独立验证与审查仍分离。

- 消费者进程清理继续按实际失败层收敛：r7 因实现交回后的输入漂移为 FAIL，保留原冻结与记录；r8/r9 的 quality PASS，但外层 TERM 回归偶发退出 1，后续全量检查未运行。新增仅测试 stderr 后，独立重复诊断取得真实栈：handler 已产生退出 143，finally 的安全清理拒绝覆盖了退出状态。进一步只在 ignored runner 插桩的 `runtime-consumer-libproc-diagnostic-f8c1245666fa4b1da4c6c094988c45e0/` 第 4 次复现，精确组中自有孙进程在 EPERM 后仍报告状态 2，而非 zombie 状态 5；不是枚举缺项或截断，不得直接忽略权限错误。正在补有界状态过渡核验，持续非 zombie/不可确认仍 FAIL，保留 0.5 秒外层终止约束。实际双平台发布、完整 Verify/Formal 均未完成，注册仍未切换。

- 消费者 r10 独立六命令全 PASS：quality 3 项、非插桩 TERM 连续 20 次、直接测试 37 项、verification 全套 359 项（均零失败/错误/跳过）、planning 52 Task/12 checks、policy 无 drift。468 项冻结输入父复算零差异，所有自有进程组与临时目录已清理；证据 `phase2-release-validation/runtime-consumer-validation-r10-a3c164a9063147a2af1fa9172f681663/`，独立复审 `runtime-consumer-review-r10.md` 零实质阻断。Darwin EPERM 仅在未回收 leader 固定身份期间、50ms/12 轮内确证全组 zombie 才容忍；持续 live/unknown 仍 FAIL。此前输入漂移与失败证据全部保留。
- 在上述代码与测试闭环后，两个 lifecycle-runtime Check 已同时改接 `python3 -m scripts.environment.release_runtime_check`，显式声明 Java25、隔离测试 JDBC、双原生 Docker Unix 端点及离线缓存，保留标准结果合同与1200秒检查时限。声明断言和实际入口无 Verify 输入时的 BLOCKED 回归同步；接线后的独立验证尚待执行，r10 不能替代新输入验收。未运行真实 Docker、宿主端口/Chrome/真实视频/资源预算验收，也未签完整 Verify 或 Formal。

- runtime 接线独立 r2 五命令 PASS：quality 3 项、6 模块直接测试 63 项、verification 359 项、planning 52 Task/12 checks、policy 无 drift；468 项冻结父复算零差异，自有进程/临时目录清理。证据 `runtime-consumer-wiring-validation-r2-b6747732b5f2452f886a36a9e4e5874a/`，独立 `runtime-consumer-wiring-review-r2.md` 接线范围零实质发现。r1 因旧结果测试未隔离新增环境要求而失败，修复测试 seam 后验证，不改 kernel 或削弱必需环境。
- 接线复审另确认 npm 全局配置读取尚未显式隔离，父已令实际构件消费者固定 `npm_config_globalconfig` 为 null device，增加毒化调用环境断言，并将原许可布尔常量断言改为真实调用消费者的拒绝测试；该新增输入待独立验证。下一步可在 Harness 隔离 PostgreSQL 与明确离线缓存下验证真实来源桥接→本机构件/两代合成资料，不需等待双 Docker；不将此当作真实双平台发布通过。只读前置检查当前缺显式双平台 Unix 端点、测试 JDBC 和缓存变量，工具与 Java25 可用；测试 JDBC 可由既有 Harness 自行准备，端点缺失仍单列为环境边界。

- npm 配置隔离增量独立验证 r2 五命令 PASS（quality 3、artifact 直接测试 2、verification 359、planning/policy），468 项冻结父复算零差异；独立 `runtime-artifacts-review-r2.md` 零实质发现，r1 的 runner 日志独占创建错误保留、不算已运行测试。
- 已完成真正来源桥接→本机构件生成的独立执行：`runtime-local-artifacts-execution-r1-e6caa0ff-2f27-439d-9136-ddff269105ac/` 消费同一次原始源码快照（87 input paths、377 文件、无缺失），在私有规范路径源码副本而非原 checkout 执行 Java25 离线 bootJar、npm 离线 ci/build、扩展 ZIP 与真实两代合成资料 producer 五步，总计100.07秒。逐字节核对 JAR 25,028,936 bytes、扩展 ZIP 31,094 bytes、两代资料4013/4017 bytes，资料版本1/2且摘要不同；未读取真实词库或运行配置。独立review `runtime-local-artifacts-review-r1.md` 零实质发现，父重算468项源码冻结零差异。Harness PostgreSQL lease `368f1eb6b0614e37b60cf3512b937a6d` 已精确清理，原lease/cleanup记录与证据副本保留，自有scratch删除。该副本仍明确 `verificationRunBound=false`、`formalReleaseEligible=false`；真实本地构件机制验证不能替代 Docker镜像、双原生平台、浏览器/真实视频、资源预算、来源许可、完整Verify/Formal。

- OPS-2011/OPS-2007 已补首次安装残根的失败关闭诊断：`prepare` 在 Docker 查询或锁/状态写入前只读核对既有 owner/state/engine，无法确认时固定 `LF_INSTALLATION_UNVERIFIED`，保留目录原状并指导选未占用新私有路径，不承诺接管或同路径自动恢复。独立定向验证 `owner-root-diagnostic-validation-3c026993-070b-4bf2-a655-768c7f753693/` 的安装4项、生命周期1项复合检查、Docker替身82项、声明回归20+6项及docs/planning/policy通过。首轮244项冻结漏Catalog，保留原证据；`owner-root-catalog-validation-9d26a660-b17c-457d-981f-62f820723637/` 补至248项，独立重跑planning/policy并断言OPS-2007公开Verify入口与六项必需checks，父复算零漂移。独立只读复审 `owner-root-diagnostic-review-3c026993-070b-4bf2-a655-768c7f753693.md` 零实质阻断，报告SHA-256 `d5cdb1a0d4e4f1f4604847e277ed99759f5b0e008393dacf2168eb0c383fe563`。此处状态记录为复审后追加，不沿用旧冻结声称当前全树通过；未运行真实Docker、完整Verify或Formal，前三项暂缓决定不变。
