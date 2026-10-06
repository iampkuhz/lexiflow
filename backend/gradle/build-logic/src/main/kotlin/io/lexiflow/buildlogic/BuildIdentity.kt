package io.lexiflow.buildlogic

import java.io.File
import org.gradle.api.GradleException

/**
 * 构建身份解析：配置阶段仅读取基础版本，完整身份由类型化任务在执行阶段生成。
 *
 * - [readBaseVersion] 在配置阶段调用，只读 ops/release/version.txt，不启动 Node/Git。
 * - [validateFullIdentity] 纯消费校验，由 [GenerateSoftwareIdentityTask] 在执行阶段调用。
 */
object BuildIdentity {
    private val fields = setOf(
        "schemaVersion", "baseVersion", "softwareVersion", "chromeVersion",
        "sourceCommit", "sourceSha256", "buildId", "dirty", "channel",
    )

    /** 配置阶段读取基础版本；只消费 ops/release/version.txt，不启动 Node/Git。 */
    fun readBaseVersion(repoRoot: File): String {
        val versionFile = File(repoRoot, "ops/release/version.txt")
        if (!versionFile.isFile) throw GradleException("软件版本文件缺失：${versionFile.absolutePath}")
        val raw = versionFile.readText(Charsets.UTF_8)
        return ReleaseVersion.parseBaseVersion(raw)
    }

    /**
     * 校验完整身份 JSON 的字段完整性、类型和版本格式，返回解析后的 Map。
     * 纯消费校验，不执行 Node/Git；由 [GenerateSoftwareIdentityTask] 在 @TaskAction 中调用。
     */
    fun validateFullIdentity(json: String): Map<String, Any?> {
        val parsed = try {
            groovy.json.JsonSlurper().parseText(json) as? Map<*, *>
        } catch (_: Exception) {
            null
        } ?: throw GradleException("构建身份输出必须是 JSON 对象")
        if (parsed.keys != fields || parsed["schemaVersion"] != 1 || parsed["dirty"] !is Boolean
            || (fields - setOf("schemaVersion", "dirty")).any { (parsed[it] as? String).isNullOrBlank() }
        ) {
            throw GradleException("构建身份字段不完整或类型无效")
        }
        ReleaseVersion.parse(parsed["softwareVersion"] as String)
        @Suppress("UNCHECKED_CAST")
        return parsed as Map<String, Any?>
    }

    /** 校验发行资格摘要与完整身份一致。 */
    fun validateReleaseQualification(
        fullIdentity: Map<String, Any?>,
        qualificationJson: String,
    ) {
        val qualification = try {
            groovy.json.JsonSlurper().parseText(qualificationJson) as? Map<*, *>
        } catch (_: Exception) {
            null
        } ?: throw GradleException("发行资格输出必须是 JSON 对象")
        val version = ReleaseVersion.parse(fullIdentity["softwareVersion"] as String)
        if (qualification.keys != setOf("softwareVersion", "sourceCommit")
            || fullIdentity["dirty"] != false
            || fullIdentity["sourceCommit"] != qualification["sourceCommit"]
            || version != qualification["softwareVersion"]
        ) {
            throw GradleException("发行资格与完整构建身份不一致")
        }
    }
}
