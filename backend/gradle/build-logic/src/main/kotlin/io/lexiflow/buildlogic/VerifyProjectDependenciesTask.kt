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
        val allowed = mapOf(
            "module" to setOf("module"),
            "application" to setOf("module"),
            "platform" to setOf("module", "application"),
            "app" to setOf("module", "application", "platform"),
            "test" to setOf("module", "application", "platform", "app"),
        )
        graph.forEach { (source, targets) ->
            val sourceRole = role(source)
            targets.forEach { target ->
                val targetRole = role(target)
                if (targetRole !in allowed.getValue(sourceRole)) {
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

    private fun role(path: String): String = when {
        path.startsWith(":modules:") -> "module"
        path.startsWith(":application:") -> "application"
        path.startsWith(":platform:") -> "platform"
        path.startsWith(":apps:") -> "app"
        path.startsWith(":tests:") -> "test"
        else -> throw GradleException("Unknown project role: $path")
    }
}
