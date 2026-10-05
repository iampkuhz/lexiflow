package io.lexiflow.buildlogic

import java.io.File
import org.gradle.testfixtures.ProjectBuilder
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

/** 身份资源逐字消费声明输入；不在生成阶段重算或截断 JSON。 */
class GenerateSoftwareIdentityTaskTest {
    @TempDir lateinit var directory: File

    @Test
    fun writesExactIdentityAndVersionResources() {
        val project = ProjectBuilder.builder().withProjectDir(directory).build()
        val task = project.tasks.register("generateIdentity", GenerateSoftwareIdentityTask::class.java).get()
        val json = """{"softwareVersion":"1.2.3","sourceCommit":"synthetic","extra":"preserved"}"""
        task.identityJson.set(json)
        task.softwareVersion.set("1.2.3")
        task.outputDirectory.set(File(directory, "identity"))
        task.generate()
        assertEquals("$json\n", File(directory, "identity/META-INF/lexiflow-build.json").readText())
        assertEquals("1.2.3\n", File(directory, "identity/META-INF/lexiflow-version.txt").readText())
    }
}
