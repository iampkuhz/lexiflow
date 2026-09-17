# Acceptance Case Registry

Acceptance case 是产品要求、实现任务与可执行测试之间的稳定连接。ID 一经使用不复用；废弃时保留记录并标记 `retired`。

每条 case 至少包含：

- `Case id`：新用例默认 `LF-AC-<DOMAIN>-<NNN>`；已分配的 `LF-<DOMAIN>-<NNN>` 与 `ARCH/FLOW/LEARN/SOURCE/TENANT/FAIL/OBS-<NNN>` 保持稳定，不复用；
- `Priority`：P0/P1/P2；
- `Requirement source`；
- `Given / When / Then`；
- `Evidence owner` 与未来测试位置；
- `Status`：proposed/accepted/implemented/verified/retired。

OpenSpec 描述长期行为，case 描述可观察验收，测试负责产生证据。仅存在映射不代表测试已经运行。
