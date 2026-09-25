package io.lexiflow.buildlogic

import org.gradle.api.GradleException
import org.gradle.testfixtures.ProjectBuilder
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

/** 验证领域项目图拒绝越界、空缺及伪装新增项目，不依赖真实产品编译。 */
class VerifyProjectDependenciesTaskTest {
    @TempDir
    lateinit var directory: File

    private fun graph(): MutableMap<String, List<String>> = linkedMapOf(
        ":modules:lexicon" to emptyList(),
        ":modules:enrichment" to listOf(":modules:lexicon"),
        ":platform:adapters" to listOf(":modules:lexicon"),
        ":apps:api" to listOf(":modules:lexicon", ":modules:enrichment", ":platform:adapters"),
        ":apps:worker" to emptyList(),
        ":tests:architecture" to listOf(":modules:lexicon", ":modules:enrichment", ":platform:adapters", ":apps:api", ":apps:worker"),
        ":tests:quality-gates" to emptyList(),
        ":tests:integration" to listOf(":platform:adapters"),
    )

    private fun verify(graph: Map<String, List<String>>) {
        val project = ProjectBuilder.builder().withProjectDir(directory).build()
        val task = project.tasks.create("verifyBoundary", VerifyProjectDependenciesTask::class.java)
        task.dependencyGraph.set(graph)
        task.resultFile.set(File(directory, "result.txt"))
        task.verify()
    }

    @Test
    fun acceptsDeclaredDomainGraph() {
        verify(graph())
        assertEquals("PASS\n", File(directory, "result.txt").readText())
    }

    @Test
    fun rejectsReverseDomainDependency() {
        val graph = graph()
        graph[":modules:lexicon"] = listOf(":modules:enrichment")
        val failure = assertThrows(GradleException::class.java) { verify(graph) }
        assertTrue(failure.message!!.contains("Forbidden project dependency"))
    }

    @Test
    fun rejectsAdapterDependencyOnEnrichment() {
        val graph = graph()
        graph[":platform:adapters"] = listOf(":modules:enrichment")
        assertThrows(GradleException::class.java) { verify(graph) }
    }

    @Test
    fun rejectsUnusedWorkerDependency() {
        val graph = graph()
        graph[":apps:worker"] = listOf(":modules:lexicon")
        assertThrows(GradleException::class.java) { verify(graph) }
    }

    @Test
    fun rejectsMissingAndUnknownProjects() {
        val missing = graph()
        missing.remove(":modules:lexicon")
        assertThrows(GradleException::class.java) { verify(missing) }
        val unknown = graph()
        unknown[":application:workflow"] = emptyList()
        assertThrows(GradleException::class.java) { verify(unknown) }
    }
}
