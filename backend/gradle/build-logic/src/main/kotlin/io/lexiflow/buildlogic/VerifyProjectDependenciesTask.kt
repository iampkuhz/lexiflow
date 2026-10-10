package io.lexiflow.buildlogic

import org.gradle.api.DefaultTask
import org.gradle.api.GradleException
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.MapProperty
import org.gradle.api.tasks.CacheableTask
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.TaskAction

/** 验证模块化单体项目依赖方向与无环约束。 */
@CacheableTask
abstract class VerifyProjectDependenciesTask : DefaultTask() {

    /** 保存项目路径到直接项目依赖的稳定映射。 */
    @get:Input
    abstract val dependencyGraph: MapProperty<String, List<String>>

    /** 保存验证摘要。 */
    @get:OutputFile
    abstract val resultFile: RegularFileProperty

    /** 检查项目全集及固定无环白名单中的分层方向。 */
    @TaskAction
    fun verify() {
        val graph = dependencyGraph.get()
        val allowed = allowedDependencies
        if (graph.keys != allowed.keys) {
            throw GradleException("Project set differs from architecture: missing=${allowed.keys - graph.keys}, unknown=${graph.keys - allowed.keys}")
        }
        graph.forEach { (source, targets) ->
            targets.forEach { target ->
                if (target !in allowed.getValue(source)) {
                    throw GradleException("Forbidden project dependency: $source -> $target")
                }
            }
        }
        val output = resultFile.get().asFile
        output.parentFile.mkdirs()
        output.writeText("PASS\n", Charsets.UTF_8)
    }

    companion object {
        // 独立测试证明固定白名单无环，逐边验证使实际合法图必然是其无环子图。
        // Gradle 项目代表领域或运行边界；同模块 domain/application 方向由 ArchUnit 检查。
        private val modules = setOf(":lexicon", ":enrichment")
        private val products = modules + setOf(":adapters", ":api")
        internal val allowedDependencies = mapOf(
            ":lexicon" to emptySet(),
            ":enrichment" to setOf(":lexicon"),
            ":adapters" to setOf(":lexicon"),
            ":api" to (modules + ":adapters"),
            ":architecture-tests" to products,
            ":quality-gates" to emptySet(),
            ":integration-tests" to setOf(":adapters"),
        )
    }

}
