package io.lexiflow.buildlogic

import java.nio.file.Files
import java.nio.file.Path
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

/** BuildIdentity 配置阶段纯读取与纯消费校验；Node 执行由 GenerateSoftwareIdentityTask 覆盖。 */
class BuildIdentityTest {
    @TempDir lateinit var repoRoot: Path

    private val commit = "abcdef0123456789abcdef0123456789abcdef01"
    private val sourceSha = "1".repeat(64)
    private val buildId = "2".repeat(64)

    private fun validIdentity(
        version: String = "1.2.3-SNAPSHOT.gabcdef0",
        sourceCommit: String = commit,
        dirty: Boolean = false,
    ) = """{"schemaVersion":1,"baseVersion":"1.2.3-SNAPSHOT","softwareVersion":"$version","chromeVersion":"1.2.3.0","sourceCommit":"$sourceCommit","sourceSha256":"$sourceSha","buildId":"$buildId","dirty":$dirty,"channel":"snapshot"}"""

    @Test
    fun `readBaseVersion returns version from file without Node`() {
        val versionFile = repoRoot.resolve("ops/release/version.txt")
        Files.createDirectories(versionFile.parent)
        Files.writeString(versionFile, "3.4.5\n")
        assertEquals("3.4.5", BuildIdentity.readBaseVersion(repoRoot.toFile()))
    }

    @Test
    fun `readBaseVersion accepts SNAPSHOT suffix`() {
        val versionFile = repoRoot.resolve("ops/release/version.txt")
        Files.createDirectories(versionFile.parent)
        Files.writeString(versionFile, "1.0.0-SNAPSHOT\n")
        assertEquals("1.0.0-SNAPSHOT", BuildIdentity.readBaseVersion(repoRoot.toFile()))
    }

    @Test
    fun `readBaseVersion rejects invalid format`() {
        val versionFile = repoRoot.resolve("ops/release/version.txt")
        Files.createDirectories(versionFile.parent)
        Files.writeString(versionFile, "not-a-version\n")
        assertThrows(Exception::class.java) { BuildIdentity.readBaseVersion(repoRoot.toFile()) }
    }

    @Test
    fun `readBaseVersion rejects missing file`() {
        assertThrows(Exception::class.java) { BuildIdentity.readBaseVersion(repoRoot.toFile()) }
    }

    @Test
    fun `validateFullIdentity accepts complete payload`() {
        val json = validIdentity()
        val result = BuildIdentity.validateFullIdentity(json)
        assertEquals(1, result["schemaVersion"])
        assertEquals("1.2.3-SNAPSHOT.gabcdef0", result["softwareVersion"])
        assertEquals("1.2.3-SNAPSHOT", result["baseVersion"])
        assertEquals(false, result["dirty"])
        assertEquals("snapshot", result["channel"])
    }

    @Test
    fun `validateFullIdentity rejects malformed payloads`() {
        val good = validIdentity()
        val malformed = listOf(
            "not-json", "", "{}",
            good.replace("\"schemaVersion\":1", "\"schemaVersion\":2"),
            good.replace("\"dirty\":false", "\"dirty\":\"false\""),
            good.replace(",\"buildId\":\"$buildId\"", ""),
            good.replace("\"softwareVersion\":\"1.2.3-SNAPSHOT.gabcdef0\"", "\"softwareVersion\":7"),
            good.replace("1.2.3-SNAPSHOT.gabcdef0", "1.2.3-SNAPSHOT.gINVALID"),
        )
        malformed.forEach { payload ->
            assertThrows(Exception::class.java, { BuildIdentity.validateFullIdentity(payload) },
                "Expected rejection for: $payload")
        }
    }

    @Test
    fun `validateReleaseQualification accepts matching qualification`() {
        val identity = BuildIdentity.validateFullIdentity(
            validIdentity(version = "1.2.3", sourceCommit = commit))
        val qualification = """{"softwareVersion":"1.2.3","sourceCommit":"$commit"}"""
        BuildIdentity.validateReleaseQualification(identity, qualification)
    }

    @Test
    fun `validateReleaseQualification rejects dirty identity`() {
        val identity = BuildIdentity.validateFullIdentity(
            validIdentity(version = "1.2.3-SNAPSHOT.gabcdef0.dirty.111111111111", dirty = true))
        val qualification = """{"softwareVersion":"1.2.3","sourceCommit":"$commit"}"""
        assertThrows(Exception::class.java) {
            BuildIdentity.validateReleaseQualification(identity, qualification)
        }
    }

    @Test
    fun `validateReleaseQualification rejects commit mismatch`() {
        val identity = BuildIdentity.validateFullIdentity(
            validIdentity(version = "1.2.3", sourceCommit = commit))
        val qualification = """{"softwareVersion":"1.2.3","sourceCommit":"${"0".repeat(40)}"}"""
        assertThrows(Exception::class.java) {
            BuildIdentity.validateReleaseQualification(identity, qualification)
        }
    }

    @Test
    fun `validateReleaseQualification rejects version mismatch`() {
        val identity = BuildIdentity.validateFullIdentity(
            validIdentity(version = "1.2.3", sourceCommit = commit))
        val qualification = """{"softwareVersion":"1.2.4","sourceCommit":"$commit"}"""
        assertThrows(Exception::class.java) {
            BuildIdentity.validateReleaseQualification(identity, qualification)
        }
    }
}
