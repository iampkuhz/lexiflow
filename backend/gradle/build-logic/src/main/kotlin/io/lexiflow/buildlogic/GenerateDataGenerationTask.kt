package io.lexiflow.buildlogic

import java.io.ByteArrayOutputStream
import java.io.File
import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.provider.Property
import org.gradle.api.provider.ProviderFactory
import org.gradle.api.tasks.Internal
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.TaskAction
import org.gradle.process.ExecOperations
import javax.inject.Inject

/** 在执行阶段计算资料生成代码身份并写入可验证的 JAR 资源。 */
abstract class GenerateDataGenerationTask : DefaultTask() {
    @get:Internal abstract val repoRoot: Property<File>
    @get:OutputDirectory abstract val outputDirectory: DirectoryProperty
    @get:Internal abstract val script: Property<File>
    @Inject protected abstract fun getExecOperations(): ExecOperations
    @Inject protected abstract fun getProviders(): ProviderFactory

    init { outputs.upToDateWhen { false }; outputs.doNotCacheIf("身份绑定当前生成输入") { true } }

    @TaskAction fun generate() {
        val output = ByteArrayOutputStream()
        val node = nodeExecutable(getProviders().environmentVariable("PATH").getOrElse(""))
        getExecOperations().exec {
            commandLine(node, script.get().absolutePath, "--output-dir", outputDirectory.get().asFile.absolutePath)
            workingDir(repoRoot.get())
            standardOutput = output
        }
    }
}
