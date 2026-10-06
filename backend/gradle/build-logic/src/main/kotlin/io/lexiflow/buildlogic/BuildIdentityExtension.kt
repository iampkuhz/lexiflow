package io.lexiflow.buildlogic

/**
 * 根项目与模块共享的配置阶段身份扩展。
 *
 * 仅持有 [baseVersion]，来自 ops/release/version.txt，在配置阶段已求值。
 * 完整身份由 [GenerateSoftwareIdentityTask] 在执行阶段生成，不通过此扩展传递。
 */
class BuildIdentityExtension(val baseVersion: String)
