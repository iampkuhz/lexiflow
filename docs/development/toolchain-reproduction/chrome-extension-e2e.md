# 1. Chrome 扩展双语提示体验与 E2E

## 1.1. 可见交互与失败边界

扩展只在 YouTube 播放页同时发现 `video`、`.html5-video-player` 和
`.ytp-caption-segment` 时工作。原始英文字幕节点从不修改、隐藏或等待网络；Chrome 扩展在独立
Shadow DOM 覆盖层中仅显示 API 已确认的中文词段释义。因此页面上的英文来自 YouTube，中文提示
位于其下方；`READY` 以外的状态只保留英文。

这不是整句翻译功能。`POST /api/v1/caption-hints` 返回的是规则批准的词或短语 `chineseGloss`，
没有中文整句字段。未命中的字幕、`NO_PENDING`、超长字幕、无法取得所需页面元素、服务端拒绝、
网络错误和超时都不会展示中文，也不会阻塞英文。

浏览器端把连续 DOM 改动先收敛 50 ms，再将活动字幕请求收敛 150 ms。新字幕会取消旧请求；旧响应
即使仍然返回，也因观察序号不匹配而被丢弃。service worker 为每个请求设置 1.5 秒时限；失败结果
不会自动重试、轮询或累积队列。切换字幕、刷新页面或重启 API 后出现新的字幕事件才会触发下一次
独立请求。

## 1.2. 本机构建、安装与体验

在一个终端启动 API：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend :apps:api:bootRun --args=--server.port=18080
```

在另一个终端构建扩展：

```bash
cd extension
npm ci
npm run build
```

打开 `chrome://extensions`，启用开发者模式，选择“加载已解压的扩展程序”，并选择
`/Users/zhehan/Documents/tools/llm/lexiflow/extension/dist`。进入有英文字幕的 YouTube 播放页并打开
字幕：英文应立即继续由 YouTube 显示；词库命中时，播放器内独立覆盖层出现括号中的中文提示。

若没有中文提示，先确认字幕是英文且 API 终端仍在运行；再刷新播放页或切换到下一条字幕。扩展不
保存真实字幕、观看历史或失败请求，因此不会在后台补发旧字幕。开发者工具可检查
`#lexiflow-caption-overlay[data-lexiflow-state]`：`ready` 是可见提示，`no-pending` 是服务端确定没有
提示，`fallback` 是 API/网络降级，`idle` 是没有可处理的字幕或输入超出合同。该属性不包含字幕、
URL、中文提示或其他用户数据。

## 1.3. 自动化验证

```bash
cd extension
npm ci
npx playwright install chromium
npm test
npm run e2e
```

`npm test` 执行纯状态机测试：重复 DOM 改动只产生一次请求；替换会取消旧请求且迟到结果不渲染；
`NO_PENDING`、网络失败和来源消失都以仅英文结束且不重试。

`npm run e2e` 以 Chromium 加载构建后的 Manifest V3 扩展，并提供一个含 `video`、播放器和 YouTube
字幕节点的合成页面。它通过真实本机 API 验证词库命中与空结果，再验证快速字幕替换不会留下旧中文、
501 字符输入不发请求、以及 service worker 请求失败时英文仍保留。端口 `18080` 已有健康 API 时，
测试复用它；否则测试自行启动并在结束时停止该进程。

## 1.4. 来源合同限制

YouTube 渲染 DOM 只可靠地给出屏幕上的文本快照和播放器读数，并不公开稳定字幕轨道标识、字幕
修订或每段的真实开始/结束时间。扩展把视频身份变化作为本机观察修订，并把观察序号、播放器时间和
文本编入请求片段摘要；这些字段只用于本次同步请求的去重、取消和迟到结果拒绝，不得作为共享缓存键、
持久内容修订或异步投递绑定。

端到端测试验证扩展与 API 的真实浏览器运行链路，但不使用真实 YouTube 内容、登录、广告、DRM、
网络条件或页面私有 API。真实安装反馈应聚焦页面选择器、字幕轨道切换和播放器布局；出现不兼容时，
预期安全行为是只有原始英文字幕，而不是卡住或显示过期中文。
