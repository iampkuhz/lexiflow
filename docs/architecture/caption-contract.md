# 1. 字幕内容合同

`CaptionContext` 是 Enrichment 的版本化输入值，不形成新的业务领域。来源适配器把来源专有
字幕转成这个合同；请求内身份支持关联和失效，不授权持久保存字幕。来源取得步骤见
[来源适配合同](source-contract.md)，词库查询边界见[共享词库合同](lexicon-contract.md)。

## 1.1. ContentRevision 与来源引用

一个 `ContentRevision` 以 `content_id` 和正整数 `content_revision` 唯一标识一次
不可变的规范化字幕输入。这是输入合同，不是字幕存储要求；它携带受控的 `source_kind`、不可逆 `source_reference_digest`、
规范化算法版本和有序的 `CaptionSegment`。原始 URL、DOM、播放器状态、cookie 和观看历史
不得进入该聚合。

同一 `(content_id, content_revision)` 一经发布不可修改；来源字幕变化、规范化算法变化或
轨道变化必须创建新的 revision。相同来源引用不能自动认定为相同文本，文本摘要与规范化
版本也必须相同才可复用已有 revision。

## 1.2. CaptionSegment、时间线与稳定身份

每个 `CaptionSegment` 保存 `segment_id`、轨道标识、顺序号、半开时间区间
`[start_millis, end_millis)` 与非空规范英文正文。`segment_id` 由 content revision、轨道、
顺序、时间区间、规范文本和规范化算法版本的 SHA-256 摘要派生；因此同一输入重放得到同一
segment identity，不同 revision 不共享 segment identity。

1. `start_millis` 非负且严格小于 `end_millis`；同一轨道的顺序号连续递增，时间不得倒退。
2. 同一轨道不允许重叠；来源明确给出的多轨重叠必须使用不同轨道标识，不得混为一条时间线。
3. 每个用于提示的范围是该 segment 规范正文内的半开字符区间 `[start_offset, end_offset)`；
   空范围、越界范围或未指明 segment 的范围都被拒绝。
4. 结果和缓存键必须同时带 content revision、segment identity、范围和各自的合同版本；
   新 revision、轨道或算法版本不能复用旧结果。

## 1.3. 规范化与输入限制

首版英文规范化按固定顺序执行 Unicode NFC、换行统一为空格、连续 Unicode 空白压缩为一个
ASCII 空格，并删除首尾空白。字母、数字、内部撇号和连字符保持原样；不翻译、不校正拼写、
不删除标点，也不跨 segment 合并文本。`normalization_version` 是 `ContentRevision` 的必填字段。

单个 segment 最大 500 UTF-16 code unit，单次 `CaptionContext` 只携带当前 segment 及有限
相邻上下文；超过上限、缺少英文文本、无法定位 revision 或无法计算稳定身份时，来源适配器
必须安全降级为仅英文，不能伪造字幕或提示。

## 1.4. provenance 与失效

`source_kind` 只用于选择来源适配器，核心领域不得按来源专有字段分支。`source_reference_digest`
仅用于诊断同一来源对象，不能反向恢复 URL。来源可撤回、字幕被替换或完整性摘要不匹配时，
对应 revision 的结果不可展示或复用。不因版本关联持久保存字幕、观看历史或当前字幕的语义工作；
离线分析的数据边界须在第三阶段另行确认。
