package io.lexiflow.buildlogic

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

class ReleaseVersionTest {
    @Test fun acceptsBoundsAndLineEndings() {
        assertEquals("0.1.0", ReleaseVersion.parse("0.1.0\n"))
        assertEquals("65535.1.0", ReleaseVersion.parse("65535.1.0\r\n"))
    }

    @Test fun acceptsResolvedSnapshotIdentity() {
        assertEquals("2.0.0-SNAPSHOT.20261006.gabc1234.dirty.0123456789ab", ReleaseVersion.parse("2.0.0-SNAPSHOT.20261006.gabc1234.dirty.0123456789ab"))
        assertThrows(IllegalArgumentException::class.java) { ReleaseVersion.parse("2.0.0-SNAPSHOT") }
    }

    @Test fun rejectsInvalidOrMissingSourceDates() {
        assertEquals("2.0.0-SNAPSHOT.20240229.gabc1234", ReleaseVersion.parse("2.0.0-SNAPSHOT.20240229.gabc1234"))
        listOf("20260229", "20260431", "20261301", "20260001", "00000101", "2026106").forEach {
            assertThrows(IllegalArgumentException::class.java) { ReleaseVersion.parse("2.0.0-SNAPSHOT.$it.gabc1234") }
        }
        assertThrows(IllegalArgumentException::class.java) { ReleaseVersion.parse("2.0.0-SNAPSHOT.gabc1234") }
    }

    @Test fun rejectsInvalidValues() {
        listOf("0.0.0", "01.2.3", "1.2.65536", "1.2.3\n\n", " 1.2.3", "1.2.3x", "\uFEFF1.2.3").forEach {
            assertThrows(IllegalArgumentException::class.java) { ReleaseVersion.parse(it) }
        }
    }
}
