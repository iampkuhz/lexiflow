# 1. 工具链复现

工具链复现的核心不是在开发机上再次构建成功，而是证明另一个干净环境能用同一组受支持输入得到同一类构建结果。固定目标是：精确运行时版本、仓库内 Wrapper、严格依赖锁、干净环境重建和可复核证据。

## 1.1. 核心复现模型

运行时版本、Wrapper 版本、依赖锁和仓库配置共同构成复现输入；系统默认 Java、全局 Gradle、缓存命中或历史构建产物都不属于可证明的输入。先由官方兼容性事实审定精确版本，再把选择固定到仓库控制的入口；干净环境只从这些入口重建。

复现记录必须保留输入版本、来源、实际命令、环境差异和结果边界。环境或兼容性不一致时记 `BLOCKED` 或 `FAIL`，先更新 ADR/OpenSpec，不能静默替换依赖或把本机偶然成功写成兼容结论。

## 1.2. 责任与阶段边界

`LF-WS-OPS` 负责后续复现环境与锁定；`LF-WS-QLT` 独立运行验证并保存不可覆盖收据。已有 Java 底座只证明其已存在，不证明双环境复现或产品业务完成；阶段出口仍以[长期计划](../roadmap/master-plan.md#p1--architecture)和[第一阶段状态页](../roadmap/phase-1-status.md)为准。

## 1.3. 详细流程入口

- [运行时、Wrapper 与依赖锁](toolchain-reproduction/runtime-and-locks.md)
- [干净环境重建](toolchain-reproduction/clean-environment.md)
- [证据、回退与持续审查](toolchain-reproduction/evidence-and-review.md)

## 1.4. 边界

本页保留复现原则、责任和证明边界；版本清点命令、锁定步骤、环境清理和证据格式只在同名目录 [`toolchain-reproduction/`](toolchain-reproduction/) 中维护。
