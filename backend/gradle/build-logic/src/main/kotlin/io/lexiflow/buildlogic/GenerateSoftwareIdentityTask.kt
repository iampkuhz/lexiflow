package io.lexiflow.buildlogic

import java.io.File
import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputFile
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import javax.inject.Inject

/**
 * 类型化身份生成任务：在执行阶段调用 Node 生成完整身份。
 *
 * - 配置阶段不执行 Node/Git，仅声明输入/输出。
 * - 执行阶段通过 [ExecOperations] 调用 node ops/release/version.mjs。
 * - 不使用 @CacheableTask，因为身份依赖 Git 状态（dirty/sourceSha256）。
 * - outputs.upToDateWhen { false } 确保每次执行都重新生成。
 */
abstract class GenerateSoftwareIdentityTask : DefaultTask() {
    @get:Internal
    abstract val repoRoot: Property<File>

    @get:Input
    abstract val release: Property<Boolean>

    @get:InputFile
    abstract val versionFile: RegularFileProperty

    @get:InputFile
    abstract val versionScript: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @get:OutputFile
    abstract val manifestFile: RegularFileProperty

    @Inject
    protected abstract fun getExecOperations(): ExecOperations

    init {
        outputs.upToDateWhen { false }
        outputs.doNotCacheIf("身份绑定当前 Git 和源码状态") { true }
    }

    @TaskAction
    fun generate() {
        val root = repoRoot.get()
        val script = versionScript.get().asFile.absolutePath

        fun runNode(vararg arguments: String): String {
            val output = java.io.ByteArrayOutputStream()
            getExecOperations().exec {
                commandLine(listOf("node", script) + arguments)
                workingDir(root)
                standardOutput = output
            }
            return output.toString(Charsets.UTF_8).trim()
        }

        val qualificationJson = if (release.get()) runNode("--release") else null
        val fullJson = runNode()
        val identity = BuildIdentity.validateFullIdentity(fullJson)

        if (qualificationJson != null) {
            BuildIdentity.validateReleaseQualification(identity, qualificationJson)
        }

        val softwareVersion = identity["softwareVersion"] as String
        val directory = outputDirectory.get().asFile.resolve("META-INF")
        directory.mkdirs()
        directory.resolve("lexiflow-version.txt").writeText("$softwareVersion\n", Charsets.UTF_8)
        directory.resolve("lexiflow-build.json").writeText("$fullJson\n", Charsets.UTF_8)
        val manifest = java.util.jar.Manifest()
        manifest.mainAttributes.putValue("Manifest-Version", "1.0")
        manifest.mainAttributes.putValue("Implementation-Version", softwareVersion)
        val target = manifestFile.get().asFile
        target.parentFile.mkdirs()
        target.outputStream().use { manifest.write(it) }
    }
}
