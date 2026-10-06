package io.lexiflow.buildlogic

import java.io.File
import java.io.Serializable
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.jar.Manifest
import java.util.zip.ZipFile
import org.gradle.api.Action
import org.gradle.api.GradleException
import org.gradle.api.Task
import org.gradle.api.tasks.bundling.AbstractArchiveTask

/**
 * bootJar 的最后一个动作：从稳定的内部归档导出唯一对外版本名。
 * 输入与输出由调用处声明在 bootJar 上；这里只保存路径，不捕获 Project 或构建脚本。
 */
class PublishVersionedBootJarAction(
    private val identityDirectoryPath: String,
    private val publicDirectoryPath: String,
) : Action<Task>, Serializable {
    override fun execute(task: Task) {
        val archive = task as? AbstractArchiveTask
            ?: throw GradleException("版本化导出只适用于归档任务")
        val source = archive.archiveFile.get().asFile
        val identityDirectory = File(identityDirectoryPath)
        val jsonFile = File(identityDirectory, "META-INF/lexiflow-build.json")
        val versionFile = File(identityDirectory, "META-INF/lexiflow-version.txt")
        val json = jsonFile.readText(Charsets.UTF_8)
        val identity = BuildIdentity.validateFullIdentity(json.trimEnd('\n'))
        val softwareVersion = identity["softwareVersion"] as String
        val versionText = "$softwareVersion\n"
        if (versionFile.readText(Charsets.UTF_8) != versionText) {
            throw GradleException("构建身份版本资源不一致")
        }

        ZipFile(source).use { zip ->
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

        val publicDirectory = File(publicDirectoryPath).toPath()
        Files.createDirectories(publicDirectory)
        Files.copy(source.toPath(), publicDirectory.resolve("api-$softwareVersion.jar"),
            StandardCopyOption.REPLACE_EXISTING)
    }

}
