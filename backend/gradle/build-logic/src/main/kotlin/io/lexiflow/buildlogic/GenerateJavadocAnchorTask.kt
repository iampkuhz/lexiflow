package io.lexiflow.buildlogic

import org.gradle.api.DefaultTask
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.tasks.CacheableTask
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.TaskAction

/** 为仅包含 package-info 的边界模块生成构建期 Javadoc 锚点。 */
@CacheableTask
abstract class GenerateJavadocAnchorTask : DefaultTask() {

    /** 构建目录内的生成源码。 */
    @get:OutputFile
    abstract val outputFile: RegularFileProperty

    /** 写入不进入产品 classpath 的最小文档类型。 */
    @TaskAction
    fun generate() {
        val output = outputFile.get().asFile
        output.parentFile.mkdirs()
        output.writeText(
            """
            package io.lexiflow.generated.javadoc;

            /** 使仅含 package-info 的边界模块也能运行 DocLint。 */
            final class JavadocAnchor {}
            """.trimIndent() + "\n",
            Charsets.UTF_8,
        )
    }
}
