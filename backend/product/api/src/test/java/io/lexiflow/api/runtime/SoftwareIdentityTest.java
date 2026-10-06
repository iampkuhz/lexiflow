package io.lexiflow.api.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class SoftwareIdentityTest {
  @Test
  void acceptsCanonicalVersionAndSingleOptionalLineEnding() {
    assertEquals("0.1.0", SoftwareIdentity.parseVersion("0.1.0"));
    assertEquals("0.1.0", SoftwareIdentity.parseVersion("0.1.0\n"));
    assertEquals("0.1.0", SoftwareIdentity.parseVersion("0.1.0\r\n"));
    assertEquals("65535.65535.65535", SoftwareIdentity.parseVersion("65535.65535.65535"));
  }

  @Test
  void acceptsResolvedSnapshotAndDirtyIdentity() {
    var value = "2.0.0-SNAPSHOT.20261006.gabc1234.dirty.0123456789ab";
    assertEquals(value, SoftwareIdentity.parseVersion(value));
    assertThrows(
        IllegalStateException.class, () -> SoftwareIdentity.parseVersion("2.0.0-SNAPSHOT"));
  }

  @Test
  void rejectsInvalidOrMissingSourceDates() {
    assertEquals(
        "2.0.0-SNAPSHOT.20240229.gabc1234",
        SoftwareIdentity.parseVersion("2.0.0-SNAPSHOT.20240229.gabc1234"));
    for (var date :
        List.of("20260229", "20260431", "20261301", "20260001", "00000101", "2026106")) {
      assertThrows(
          IllegalStateException.class,
          () -> SoftwareIdentity.parseVersion("2.0.0-SNAPSHOT." + date + ".gabc1234"));
    }
    assertThrows(
        IllegalStateException.class,
        () -> SoftwareIdentity.parseVersion("2.0.0-SNAPSHOT.gabc1234"));
  }

  @Test
  void rejectsNonCanonicalOutOfRangeAndExtraLineEndingVersions() {
    for (var invalid : List.of("01.2.3", "1.2.3\n\n", "65536.1.1", "0.0.0", "1.2.3\r")) {
      assertThrows(IllegalStateException.class, () -> SoftwareIdentity.parseVersion(invalid));
    }
  }
}
