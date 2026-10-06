package io.lexiflow.buildlogic

import org.gradle.api.file.RegularFileProperty
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.process.CommandLineArgumentProvider

/** 测试 JVM 的完整版本来自生成资源，而非配置阶段的基础版本。 */
abstract class IdentityVersionArgumentProvider : CommandLineArgumentProvider {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val identityVersionFile: RegularFileProperty

    override fun asArguments(): Iterable<String> =
        listOf("-Dlexiflow.build.version=${identityVersionFile.get().asFile.readText(Charsets.UTF_8).trim()}")
}
