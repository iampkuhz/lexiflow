package io.lexiflow.buildlogic

import java.nio.file.Files
import java.nio.file.Path
import org.gradle.testfixtures.ProjectBuilder
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class BuildIdentityTest {
    @TempDir lateinit var repoRoot: Path

    private val commit = "abcdef0123456789abcdef0123456789abcdef01"
    private val sourceSha = "1".repeat(64)
    private val buildId = "2".repeat(64)

    private fun invoke(script: String) {
        val nodeScript = repoRoot.resolve("ops/release/version.mjs")
        Files.createDirectories(nodeScript.parent)
        Files.writeString(nodeScript, script)
    }

    private fun validIdentity(
        version: String = "1.2.3-SNAPSHOT.gabcdef0",
        sourceCommit: String = commit,
        dirty: Boolean = false,
    ) = """{"schemaVersion":1,"baseVersion":"1.2.3-SNAPSHOT","softwareVersion":"$version","chromeVersion":"1.2.3.0","sourceCommit":"$sourceCommit","sourceSha256":"$sourceSha","buildId":"$buildId","dirty":$dirty,"channel":"snapshot"}"""

    private fun resolve(release: Boolean = false): BuildIdentityExtension =
        BuildIdentity.resolve(ProjectBuilder.builder().build().providers, repoRoot.toFile(), release)

    private fun nodeFixture(
        releaseOutput: String,
        fullOutput: String,
        releaseExit: Int = 0,
        fullExit: Int = 0,
        requireReleaseFirst: Boolean = false,
    ) =
        """
        import { existsSync, writeFileSync } from 'node:fs';
        const isRelease = process.argv.includes('--release');
        const marker = ${js(repoRoot.resolve("release-called").toString())};
        if ($requireReleaseFirst) {
          if (isRelease) writeFileSync(marker, 'called');
          else if (!existsSync(marker)) { process.stderr.write('full identity called before release qualification'); process.exit(9); }
        }
        const output = isRelease ? ${js(releaseOutput)} : ${js(fullOutput)};
        const exit = isRelease ? $releaseExit : $fullExit;
        process.stdout.write(output);
        process.exitCode = exit;
        """.trimIndent()

    private fun js(value: String): String =
        "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"

    @Test
    fun `resolves full identity in development mode`() {
        val json = validIdentity()
        invoke(nodeFixture("", json))
        val result = resolve()
        assertEquals(json, result.json)
        assertEquals("1.2.3-SNAPSHOT.gabcdef0", result.softwareVersion)
    }

    @Test
    fun `release qualification precedes and agrees with full identity`() {
        val json = validIdentity(version = "1.2.3", sourceCommit = commit)
        invoke(nodeFixture("{\"softwareVersion\":\"1.2.3\",\"sourceCommit\":\"$commit\"}", json,
            requireReleaseFirst = true))
        val result = resolve(release = true)
        assertEquals(json, result.json)
        assertEquals("1.2.3", result.softwareVersion)
    }

    @Test
    fun `rejects dirty release full identity`() {
        val json = validIdentity(version = "1.2.3-SNAPSHOT.gabcdef0.dirty.111111111111", dirty = true)
        invoke(nodeFixture("{\"softwareVersion\":\"1.2.3\",\"sourceCommit\":\"$commit\"}", json,
            requireReleaseFirst = true))
        assertThrows(Exception::class.java) { resolve(release = true) }
    }

    @Test
    fun `rejects release qualification commit or version mismatch`() {
        val json = validIdentity(version = "1.2.3", sourceCommit = commit)
        invoke(nodeFixture("{\"softwareVersion\":\"1.2.3\",\"sourceCommit\":\"${"0".repeat(40)}\"}", json,
            requireReleaseFirst = true))
        assertThrows(Exception::class.java) { resolve(release = true) }
        invoke(nodeFixture("{\"softwareVersion\":\"1.2.4\",\"sourceCommit\":\"$commit\"}", json,
            requireReleaseFirst = true))
        assertThrows(Exception::class.java) { resolve(release = true) }
    }

    @Test
    fun `rejects invalid complete identity payloads`() {
        val good = validIdentity()
        val malformed = listOf(
            "not-json", "", "{}", good.replace("\"schemaVersion\":1", "\"schemaVersion\":2"),
            good.replace("\"dirty\":false", "\"dirty\":\"false\""),
            good.replace(",\"buildId\":\"$buildId\"", ""),
            good.replace("\"softwareVersion\":\"1.2.3-SNAPSHOT.gabcdef0\"", "\"softwareVersion\":7"),
            good.replace("1.2.3-SNAPSHOT.gabcdef0", "1.2.3-SNAPSHOT.gINVALID"),
        )
        malformed.forEach { payload ->
            invoke(nodeFixture("", payload))
            assertThrows(Exception::class.java, { resolve() }, "Expected rejection for: $payload")
        }
    }

    @Test
    fun `accepts dirty development and clean snapshot release`() {
        val dirty = validIdentity(version = "1.2.3-SNAPSHOT.gabcdef0.dirty.111111111111", dirty = true)
        invoke(nodeFixture("", dirty))
        assertEquals(dirty, resolve().json)
        val clean = validIdentity()
        invoke(nodeFixture("{\"softwareVersion\":\"1.2.3-SNAPSHOT.gabcdef0\",\"sourceCommit\":\"$commit\"}", clean,
            requireReleaseFirst = true))
        assertEquals(clean, resolve(release = true).json)
    }

    @Test
    fun `rejected release qualification prevents full identity call`() {
        invoke(nodeFixture("", validIdentity(), releaseExit = 3, requireReleaseFirst = true))
        assertThrows(Exception::class.java) { resolve(release = true) }
    }

    @Test
    fun `rejects unsuccessful Node execution`() {
        invoke(nodeFixture("", validIdentity(), fullExit = 7))
        assertThrows(Exception::class.java) { resolve() }
    }
}
