package io.lexiflow.api.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class RuntimeInitializationTest {
  private static final String SHA = "a".repeat(64);
  private static final String IDENTITY =
      "{\"schemaVersion\":1,\"softwareVersion\":\"1.2.3\",\"buildIdentity\":{\"softwareVersion\":\"1.2.3\"},\"apiContract\":\"caption-hints.v2\",\"sqlVersion\":\"sql1\",\"dataset\":{\"releaseId\":\"rel\",\"preparationId\":\"prep\",\"ruleId\":\"rule\",\"bytes\":2,\"sha256\":\""
          + SHA
          + "\"}}";
  private static final String PROOF =
      "{\"schemaVersion\":1,\"cliResult\":\"PASS\",\"datasetVersion\":42,\"releaseId\":\"rel\",\"preparationId\":\"prep\",\"ruleId\":\"rule\",\"sqlVersion\":\"sql1\",\"sha256\":\""
          + SHA
          + "\"}";

  @Test
  void sourceRuntimeKeepsLiveProbeSemanticsWithoutClaimingReleaseBinding(@TempDir Path dir)
      throws Exception {
    var runtime =
        new RuntimeInitialization(
            dir.resolve("gate"), dir.resolve("identity"), dir.resolve("proof"), false);
    var state = new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 42L, true);

    var result = runtime.evaluate(state, "1.2.3");

    assertTrue(result.ready());
    assertEquals(42L, result.datasetVersion());
    assertNull(result.publishedDataset());
  }

  @Test
  void explicitGateReportsInitializingWithoutStaticDatasetBinding(@TempDir Path dir)
      throws Exception {
    Path gate = dir.resolve("initializing");
    Files.createFile(gate);
    var runtime =
        new RuntimeInitialization(gate, dir.resolve("identity"), dir.resolve("proof"), true);

    var result =
        runtime.evaluate(
            new LexiconRuntime.State("formal", LexiconRuntime.Reason.SCHEMA_MISMATCH, null, false),
            "1.2.3");

    assertFalse(result.ready());
    assertEquals(LexiconRuntime.Reason.INITIALIZING, result.reason());
    assertNull(result.publishedDataset());
  }

  @Test
  void releaseBindingRequiresExactPerBootProofAndMatchingLiveVersion(@TempDir Path dir)
      throws Exception {
    Path identity = dir.resolve("release-identity.json");
    Path proof = dir.resolve("verified.json");
    var runtime = new RuntimeInitialization(dir.resolve("gate"), identity, proof, true);
    Files.writeString(identity, IDENTITY);
    Files.writeString(proof, PROOF);

    var verified =
        runtime.evaluate(
            new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 42L, true), "1.2.3");
    assertTrue(verified.ready());
    assertEquals("rel", verified.publishedDataset().releaseId());
    assertEquals(SHA, verified.publishedDataset().sha256());

    var mismatch =
        runtime.evaluate(
            new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 43L, true), "1.2.3");
    assertFalse(mismatch.ready());
    assertEquals(LexiconRuntime.Reason.IDENTITY_MISMATCH, mismatch.reason());
    assertNull(mismatch.publishedDataset());
  }

  @Test
  void rejectsMissingDuplicateOrTrailingProofData(@TempDir Path dir) throws Exception {
    Path identity = dir.resolve("release-identity.json");
    Path proof = dir.resolve("verified.json");
    Files.writeString(identity, IDENTITY);
    var runtime = new RuntimeInitialization(dir.resolve("gate"), identity, proof, true);
    var state = new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 42L, true);
    assertEquals(
        LexiconRuntime.Reason.IDENTITY_MISMATCH, runtime.evaluate(state, "1.2.3").reason());

    Files.writeString(proof, PROOF + " {}");
    assertEquals(
        LexiconRuntime.Reason.IDENTITY_MISMATCH, runtime.evaluate(state, "1.2.3").reason());
    Files.writeString(
        proof, PROOF.replace("\"schemaVersion\":1", "\"schemaVersion\":1,\"schemaVersion\":1"));
    assertEquals(
        LexiconRuntime.Reason.IDENTITY_MISMATCH, runtime.evaluate(state, "1.2.3").reason());
  }
}
