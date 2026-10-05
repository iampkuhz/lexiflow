package io.lexiflow.buildlogic

import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.CacheableTask
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction

/** 只从声明的输入生成身份资源，不在执行阶段捕获 Project 或构建脚本对象。 */
@CacheableTask
abstract class GenerateSoftwareIdentityTask : DefaultTask() {
    @get:Input
    abstract val identityJson: Property<String>

    @get:Input
    abstract val softwareVersion: Property<String>

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @TaskAction
    fun generate() {
        val directory = outputDirectory.get().asFile.resolve("META-INF")
        directory.mkdirs()
        directory.resolve("lexiflow-version.txt").writeText("${softwareVersion.get()}\n", Charsets.UTF_8)
        directory.resolve("lexiflow-build.json").writeText("${identityJson.get()}\n", Charsets.UTF_8)
    }
}
