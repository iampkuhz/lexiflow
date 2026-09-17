package io.lexiflow.buildlogic

import org.gradle.api.DefaultTask
import org.gradle.api.GradleException
import org.gradle.api.file.ConfigurableFileCollection
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.tasks.CacheableTask
import org.gradle.api.tasks.InputFiles
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.TaskAction
import java.util.Locale

/** 验证产品源码根只包含 Java 产品实现。 */
@CacheableTask
abstract class VerifyProductLanguageTask : DefaultTask() {

    /** 产品源码根中的所有文件。 */
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val productSources: ConfigurableFileCollection

    /** 保存验证摘要。 */
    @get:OutputFile
    abstract val resultFile: RegularFileProperty

    /** 拒绝 Python、Kotlin、Scala 或 Groovy 产品源码。 */
    @TaskAction
    fun verify() {
        val forbiddenExtensions = setOf("py", "kt", "scala", "groovy")
        val forbidden = productSources.files
            .filter { it.isFile && it.extension.lowercase(Locale.ROOT) in forbiddenExtensions }
            .map { it.invariantSeparatorsPath }
            .sorted()
        if (forbidden.isNotEmpty()) {
            throw GradleException("Non-Java product source is forbidden: ${forbidden.joinToString()}")
        }
        val output = resultFile.get().asFile
        output.parentFile.mkdirs()
        output.writeText("PASS\n", Charsets.UTF_8)
    }
}
