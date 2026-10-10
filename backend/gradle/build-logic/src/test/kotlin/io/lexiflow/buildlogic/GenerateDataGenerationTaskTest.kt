package io.lexiflow.buildlogic

import java.io.File
import org.gradle.testfixtures.ProjectBuilder
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

/** 资料身份生成使用仓库固定入口、模块输出和失败关闭，不复用旧资源。 */
class GenerateDataGenerationTaskTest {
    @TempDir lateinit var directory: File

    private fun task(scriptBody: String): GenerateDataGenerationTask {
        File(directory, "ops/dataset/dataset-generation-identity.mjs").apply {
            parentFile.mkdirs()
            writeText(scriptBody)
        }
        val project = ProjectBuilder.builder().withProjectDir(directory).build()
        return project.tasks.register("generateDataGeneration", GenerateDataGenerationTask::class.java) {
            repoRoot.set(directory)
            outputDirectory.set(File(directory, "generated"))
        }.get()
    }

    @Test
    fun derivesFixedScriptAndPreservesWorkingDirectoryAndOutput() {
        val task = task("""
            import fs from 'node:fs';
            import path from 'node:path';
            const output = process.argv[process.argv.indexOf('--output-dir') + 1];
            fs.mkdirSync(output, {recursive: true});
            fs.writeFileSync(path.join(output, 'fixture.txt'), process.cwd());
        """.trimIndent())
        task.generate()
        assertEquals(directory.canonicalPath, File(directory, "generated/fixture.txt").readText())
        assertTrue(task.outputs.files.files.contains(File(directory, "generated")))
        assertFalse(task.outputs.upToDateSpec.isSatisfiedBy(task))
    }

    @Test
    fun failedGeneratorCannotClaimSuccessfulOutput() {
        val task = task("process.exit(7);")
        assertThrows(Exception::class.java) { task.generate() }
        assertFalse(File(directory, "generated").exists())
    }
}
