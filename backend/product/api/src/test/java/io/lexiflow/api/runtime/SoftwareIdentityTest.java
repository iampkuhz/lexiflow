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
    var value = "2.0.0-SNAPSHOT.gabc1234.dirty.0123456789ab";
    assertEquals(value, SoftwareIdentity.parseVersion(value));
    assertThrows(
        IllegalStateException.class, () -> SoftwareIdentity.parseVersion("2.0.0-SNAPSHOT"));
  }

  @Test
  void rejectsNonCanonicalOutOfRangeAndExtraLineEndingVersions() {
    for (var invalid : List.of("01.2.3", "1.2.3\n\n", "65536.1.1", "0.0.0", "1.2.3\r")) {
      assertThrows(IllegalStateException.class, () -> SoftwareIdentity.parseVersion(invalid));
    }
  }
}
