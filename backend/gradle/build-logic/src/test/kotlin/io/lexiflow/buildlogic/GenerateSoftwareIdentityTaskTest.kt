package io.lexiflow.buildlogic

import java.io.File
import org.gradle.testfixtures.ProjectBuilder
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

/** 身份生成任务声明可审计输入，并且每次执行都重新读取 Git 与源码状态。 */
class GenerateSoftwareIdentityTaskTest {
    @TempDir lateinit var directory: File

    @Test
    fun declaresGeneratorInputsAndNeverReusesIdentityOutput() {
        val project = ProjectBuilder.builder().withProjectDir(directory).build()
        val version = File(directory, "version.txt").apply { writeText("1.2.3\n") }
        val script = File(directory, "version.mjs").apply { writeText("// synthetic\n") }
        val task = project.tasks.register("generateIdentity", GenerateSoftwareIdentityTask::class.java) {
            repoRoot.set(directory)
            release.set(false)
            versionFile.set(version)
            versionScript.set(script)
            outputDirectory.set(File(directory, "identity"))
            manifestFile.set(File(directory, "generated/MANIFEST.MF"))
        }.get()

        assertTrue(task.inputs.files.files.contains(version))
        assertTrue(task.inputs.files.files.contains(script))
        assertTrue(task.outputs.files.files.contains(File(directory, "identity")))
        assertFalse(task.outputs.upToDateSpec.isSatisfiedBy(task))
    }

    private fun task(scriptBody: String, releaseMode: Boolean = false): GenerateSoftwareIdentityTask {
        val project = ProjectBuilder.builder().withProjectDir(directory).build()
        val version = File(directory, "version.txt").apply { writeText("1.2.3\n") }
        val script = File(directory, "version.mjs").apply { writeText(scriptBody) }
        return project.tasks.register("generateIdentity", GenerateSoftwareIdentityTask::class.java) {
            repoRoot.set(directory)
            release.set(releaseMode)
            versionFile.set(version)
            versionScript.set(script)
            outputDirectory.set(File(directory, "identity"))
            manifestFile.set(File(directory, "generated/MANIFEST.MF"))
        }.get()
    }

    private val json = """{"schemaVersion":1,"baseVersion":"1.2.3","softwareVersion":"1.2.3","chromeVersion":"1.2.3.0","sourceCommit":"${"a".repeat(40)}","sourceSha256":"${"b".repeat(64)}","buildId":"${"c".repeat(64)}","dirty":false,"channel":"release"}"""

    @Test
    fun generatesConsistentResourcesAndNativeManifest() {
        val task = task("console.log(process.argv.includes('--release') ? JSON.stringify({softwareVersion:'1.2.3', sourceCommit:'${"a".repeat(40)}'}) : '$json');", true)
        task.generate()
        assertEquals(json + "\n", File(directory, "identity/META-INF/lexiflow-build.json").readText())
        assertEquals("1.2.3\n", File(directory, "identity/META-INF/lexiflow-version.txt").readText())
        File(directory, "generated/MANIFEST.MF").inputStream().use {
            assertEquals("1.2.3", java.util.jar.Manifest(it).mainAttributes.getValue("Implementation-Version"))
        }
    }

    @Test
    fun failedGeneratorCannotPublishIdentityResources() {
        val task = task("process.exit(7);")
        assertThrows(Exception::class.java) { task.generate() }
        assertFalse(File(directory, "identity/META-INF/lexiflow-build.json").exists())
    }

    @Test
    fun rejectsReleaseQualificationMismatchBeforeWriting() {
        val task = task("console.log(process.argv.includes('--release') ? JSON.stringify({softwareVersion:'1.2.4',sourceCommit:'${"a".repeat(40)}'}) : '$json');", true)
        assertThrows(Exception::class.java) { task.generate() }
        assertFalse(File(directory, "identity/META-INF/lexiflow-build.json").exists())
    }

}
