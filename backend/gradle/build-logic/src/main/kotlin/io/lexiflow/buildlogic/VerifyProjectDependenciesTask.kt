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

    /** 检查分层方向和依赖环。 */
    @TaskAction
    fun verify() {
        val graph = dependencyGraph.get()
        // Gradle 项目代表领域或运行边界；同模块 domain/application 方向由 ArchUnit 检查。
        val modules = setOf(":lexicon", ":enrichment")
        val products = modules + setOf(":adapters", ":api")
        val allowed = mapOf(
            ":lexicon" to emptySet(),
            ":enrichment" to setOf(":lexicon"),
            ":adapters" to setOf(":lexicon"),
            ":api" to (modules + ":adapters"),
            ":architecture-tests" to products,
            ":quality-gates" to emptySet(),
            ":integration-tests" to setOf(":adapters"),
        )
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
        val visiting = mutableSetOf<String>()
        val visited = mutableSetOf<String>()
        fun visit(node: String) {
            if (node in visiting) {
                throw GradleException("Project dependency cycle detected at $node")
            }
            if (!visited.add(node)) return
            visiting.add(node)
            graph.getValue(node).forEach(::visit)
            visiting.remove(node)
        }
        graph.keys.forEach(::visit)
        val output = resultFile.get().asFile
        output.parentFile.mkdirs()
        output.writeText("PASS\n", Charsets.UTF_8)
    }

}
