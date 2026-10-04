package io.lexiflow.buildlogic

import java.io.File
import org.gradle.api.GradleException
import org.gradle.api.provider.ProviderFactory

/** 在配置阶段消费 Node 的唯一身份规则，不跨构建缓存或接受调用者身份覆盖。 */
object BuildIdentity {
    private val fields = setOf(
        "schemaVersion", "baseVersion", "softwareVersion", "chromeVersion",
        "sourceCommit", "sourceSha256", "buildId", "dirty", "channel",
    )

    /** 发行资格与完整身份是不同接口；资格摘要不能作为制品内嵌身份。 */
    fun resolve(providers: ProviderFactory, repoRoot: File, release: Boolean): BuildIdentityExtension {
        val script = File(repoRoot, "ops/release/version.mjs").absolutePath
        fun read(vararg arguments: String): Pair<String, Map<*, *>> {
            val output = providers.exec {
                commandLine(listOf("node", script) + arguments)
                workingDir(repoRoot)
            }.standardOutput.asText.get().trim()
            val parsed = try {
                groovy.json.JsonSlurper().parseText(output) as? Map<*, *>
            } catch (_: Exception) {
                null
            } ?: throw GradleException("构建身份输出必须是 JSON 对象")
            return output to parsed
        }
        val qualification = if (release) read("--release").second else null
        val (json, identity) = read()
        // 只检查消费接口完整性；版本派生、源码摘要与哈希算法仍归 Node 所有。
        if (identity.keys != fields || identity["schemaVersion"] != 1 || identity["dirty"] !is Boolean
            || (fields - setOf("schemaVersion", "dirty")).any { (identity[it] as? String).isNullOrBlank() }
        ) {
            throw GradleException("构建身份字段不完整或类型无效")
        }
        val version = ReleaseVersion.parse(identity["softwareVersion"] as String)
        if (qualification != null && (
                qualification.keys != setOf("softwareVersion", "sourceCommit")
                    || identity["dirty"] != false
                    || identity["sourceCommit"] != qualification["sourceCommit"]
                    || version != qualification["softwareVersion"]
                )) {
            throw GradleException("发行资格与完整构建身份不一致")
        }
        return BuildIdentityExtension(json, version)
    }
}
