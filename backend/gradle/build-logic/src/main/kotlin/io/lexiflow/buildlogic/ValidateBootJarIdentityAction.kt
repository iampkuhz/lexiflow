package io.lexiflow.buildlogic

import java.io.File
import java.io.Serializable
import java.util.jar.Manifest
import java.util.zip.ZipFile
import org.gradle.api.Action
import org.gradle.api.GradleException
import org.gradle.api.Task
import org.gradle.api.tasks.bundling.AbstractArchiveTask

/** 只读校验 bootJar 的 Manifest 与内嵌构建身份，不创建第二份归档。 */
class ValidateBootJarIdentityAction(
    private val identityDirectoryPath: String,
) : Action<Task>, Serializable {
    override fun execute(task: Task) {
        val archive = task as? AbstractArchiveTask
            ?: throw GradleException("归档身份校验只适用于归档任务")
        val jsonFile = File(identityDirectoryPath, "META-INF/lexiflow-build.json")
        val versionFile = File(identityDirectoryPath, "META-INF/lexiflow-version.txt")
        val json = jsonFile.readText(Charsets.UTF_8)
        val identity = BuildIdentity.validateFullIdentity(json.trimEnd('\n'))
        val softwareVersion = identity["softwareVersion"] as String
        val versionText = "$softwareVersion\n"
        if (versionFile.readText(Charsets.UTF_8) != versionText) {
            throw GradleException("构建身份版本资源不一致")
        }

        ZipFile(archive.archiveFile.get().asFile).use { zip ->
            fun entry(name: String): String {
                val item = zip.getEntry(name) ?: throw GradleException("归档身份资源缺失：$name")
                return zip.getInputStream(item).use { it.readBytes().toString(Charsets.UTF_8) }
            }
            if (entry("META-INF/lexiflow-build.json") != json ||
                entry("META-INF/lexiflow-version.txt") != versionText
            ) {
                throw GradleException("归档身份资源与生成结果不一致")
            }
            val manifest = zip.getEntry("META-INF/MANIFEST.MF")
                ?: throw GradleException("归档 Manifest 缺失")
            val implementationVersion = zip.getInputStream(manifest).use {
                Manifest(it).mainAttributes.getValue("Implementation-Version")
            }
            if (implementationVersion != softwareVersion) {
                throw GradleException("归档 Manifest 与完整身份版本不一致")
            }
        }
    }
}
