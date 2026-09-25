# 1. 路线图状态与下一步

本页只记录当前实现状态和仍需交接的事项；目标与边界见[路线图](../master-plan.md)。单次检查结果以其绑定输入的 Verification report 为准，不用历史结果代替本次检查。

## 1.1. 当前实现

- 第一阶段观看链路保持确定性：观看不调用模型；Chrome Extension 只使用已发布资料与用户显式的本机偏好。
- 后端按 Java 25 Modular Monolith 整理词库与提示领域边界；开发期仅维护最新 schema 与逻辑。现有运行数据库不会因源码改造自动升级，切换前须单独确认结构和资料版本。
- 扩展已具备当前页面开关、英文优先绘制、分行提示和分段诊断。真实页面表现仍取决于用户重新加载扩展后的观察，不能由夹具测试替代。
- 语境不适用的词义识别仍属后续阶段；不能把候选命中率当作语义正确率。

## 1.2. 当前交付检查

本轮工作范围是脚本入口、注释门禁、Agent 分层、Delivery Gate 命名、后端边界及其调用点。变更合同在 [`openspec/changes/rename-delivery-gate-and-close-verification/`](../../../openspec/changes/rename-delivery-gate-and-close-verification/)；长期合同在 `openspec/specs/`。本地临时报告按 Harness 写入 ignored `tmp/quality/verification-reports/`，不作为仓库中的固定 PASS 证明。

Change Verify、Repository Verify 与直接测试必须在最终相同输入上分别核验。正式 Delivery Gate 的 submission、validation、review、check 需要各阶段真实独立身份及明确证据，不能由实现者自签或由本地 Verify 自动替代。Qoder 原始运行记录保留在 ignored `tmp/qoder-tasks/`，仅供身份和预算审计，不是本轮的交付签发。

## 1.3. 后续决策

在线词库重建、完整资料导入和真实视频复验需另行协调运行环境；本轮隔离测试容器不会连接或清空用户数据库。第三阶段反馈与缺失材料的来源、授权、保留和删除边界仍待该阶段前确认。
