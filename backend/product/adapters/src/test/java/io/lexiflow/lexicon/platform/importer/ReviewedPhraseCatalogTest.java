package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import org.junit.jupiter.api.Test;

class ReviewedPhraseCatalogTest {
  @Test
  void independentlyReviewedPhrasesPrepareAndPublishWithoutInventedRankOrHumanApproval() {
    var rows = ReviewedPhraseCatalog.rows();
    assertEquals(
        List.of(
            "neural tissue",
            "fatty tissue",
            "net force",
            "nerve cell",
            "spinal cord",
            "nerve fiber",
            "fatty substance"),
        rows.stream().map(r -> r.lemma()).toList());
    assertEquals(
        List.of("神经组织", "脂肪组织", "合力", "神经细胞", "脊髓", "神经纤维", "脂肪性物质"),
        rows.stream().map(r -> r.chineseGloss()).toList());
    assertEquals(rows, ReviewedPhraseCatalog.publicationRows());
    var plan = LexiconImportPlan.prepare(rows, 1, ReviewedPhraseCatalog.digest(), Instant.EPOCH);
    for (var entry : plan) {
      assertEquals(LexiconEntryKind.PHRASE, entry.entry().entryKind());
      assertEquals(0, entry.entry().priority().memoryPriority());
      assertNull(entry.row().sourceBncRank());
      assertFalse(entry.row().prewarmEligible());
      assertNull(entry.prepared().exclusionReason());
      assertEquals(entry.row().chineseGloss(), entry.prepared().gloss());
    }
  }

  @Test
  void missingUnboundOrProducerAuthoredReviewCannotAuthorizePublication() throws Exception {
    var input = text("reviewed-phrases.tsv");
    var review = text("reviewed-phrases-review.properties");
    var report = text("reviewed-phrases-review.md").getBytes(StandardCharsets.UTF_8);
    for (var bad :
        List.of(
            "",
            review.replace("independent-ai-content", "human-confirmed"),
            review.replace(
                "3b867171-029b-4a76-82ed-1325413efb31", "39e3a54c-18ca-4d26-91f2-9a852ac23dc5"),
            review.replaceFirst("evidenceSha256=[0-9a-f]+", "evidenceSha256="),
            review.replaceFirst(
                "publicEvidenceSha256=[0-9a-f]+", "publicEvidenceSha256=" + "0".repeat(64)),
            review.replaceFirst(
                "evidenceSha256=[0-9a-f]+",
                review
                    .lines()
                    .filter(line -> line.startsWith("publicEvidenceSha256="))
                    .findFirst()
                    .orElseThrow()
                    .replace("publicEvidenceSha256=", "evidenceSha256=")),
            review.replaceFirst("subjectsSha256=[0-9a-f]+", "subjectsSha256=" + "0".repeat(64)),
            review.replace("reviewedLemmas=neural tissue|", "reviewedLemmas="),
            review.replaceFirst("attachmentId=[^\\n]+", "attachmentId="),
            review.replace("reviewBasis=TASK-120:source-facts-review.md", "reviewBasis="),
            review.replace(
                "reviewBasis=TASK-120:source-facts-review.md",
                "reviewBasis=TASK-999:source-facts-review.md"))) {
      assertThrows(
          IllegalArgumentException.class,
          () ->
              ReviewedPhraseCatalog.reviewedRows(
                  input, bad.getBytes(StandardCharsets.UTF_8), report));
    }
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ReviewedPhraseCatalog.reviewedRows(
                input, review.getBytes(StandardCharsets.UTF_8), new byte[0]));
  }

  @Test
  void pendingContentIsExcludedAndChangedMeaningCannotReuseTheReview() throws Exception {
    var input = text("reviewed-phrases.tsv");
    var review = text("reviewed-phrases-review.properties").getBytes(StandardCharsets.UTF_8);
    var report = text("reviewed-phrases-review.md").getBytes(StandardCharsets.UTF_8);
    assertEquals(
        List.of(),
        ReviewedPhraseCatalog.reviewedRows(input.replace("REVIEWED", "PENDING"), review, report));
    var subset =
        ReviewedPhraseCatalog.reviewedRows(
            input.replaceFirst("REVIEWED", "PENDING"), review, report);
    assertEquals(
        List.of(
            "fatty tissue",
            "net force",
            "nerve cell",
            "spinal cord",
            "nerve fiber",
            "fatty substance"),
        subset.stream().map(r -> r.lemma()).toList());
    assertThrows(
        IllegalArgumentException.class,
        () -> ReviewedPhraseCatalog.reviewedRows(input.replace("神经组织", "薄的织物"), review, report));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ReviewedPhraseCatalog.reviewedRows(
                input.replace("TASK-120:source-facts-review.md", "self-approved"), review, report));
  }

  @Test
  void sourcesBindExactlyThePublishedSubsetRatherThanTheWholeCatalog() throws Exception {
    assertEquals(2, ReviewedPhraseCatalog.sources().size());
    assertEquals(List.of(), ReviewedPhraseCatalog.sources(List.of()));
    var subset = ReviewedPhraseCatalog.sources(List.of("neural tissue"));
    assertEquals(1, subset.size());
    assertEquals("lexiflow-reviewed-phrases-nci", subset.getFirst().sourceId());
    var input = text("reviewed-phrases.tsv").lines().toList();
    var exact = input.getFirst() + "\n" + input.get(1) + "\n";
    assertEquals(
        HexFormat.of()
            .formatHex(
                MessageDigest.getInstance("SHA-256")
                    .digest(exact.getBytes(StandardCharsets.UTF_8))),
        subset.getFirst().sourceDigest());
  }

  @Test
  void refusesUnknownLicenseUnreviewedMeaningWrongScopeAndIncompleteEvidence() throws Exception {
    var input = text("reviewed-phrases.tsv");
    for (var bad :
        List.of(
            input.replace("LicenseRef-NCI-Text-Reuse", "unknown"),
            input.replace("Codex:", "unreviewed:"),
            input.replace("REVIEWED", "CONFIRMED"),
            input.replace("神经组织", "薄的织物"),
            input.replace("neural tissue", "tissue"),
            input.replace("https://www.cancer.gov/policies/copyright-reuse", ""),
            input.replace(
                "https://www.cancer.gov/publications", "http://www.cancer.gov/publications"),
            input.replace("neural tissues", "neural network"),
            input + input.lines().skip(1).findFirst().orElseThrow() + "\n")) {
      assertThrows(IllegalArgumentException.class, () -> ReviewedPhraseCatalog.parse(bad));
    }
    for (var bad :
        List.of(
            input.replace("LicenseRef-NCI-Text-Reuse", "unknown"),
            input.replace("Codex:", "unreviewed:"),
            input.replace("REVIEWED", "CONFIRMED"),
            input.replace("https://www.cancer.gov/policies/copyright-reuse", ""),
            input.replace("neural tissues", "neural network"))) {
      assertThrows(
          IllegalArgumentException.class, () -> ReviewedPhraseCatalog.parseCandidates(bad));
    }
  }

  @Test
  void addedPendingSubjectsCannotBorrowOldReviewEvenWithMatchingBasis() throws Exception {
    var input = withPendingFixture(text("reviewed-phrases.tsv"));
    var review = text("reviewed-phrases-review.properties").getBytes(StandardCharsets.UTF_8);
    var report = text("reviewed-phrases-review.md").getBytes(StandardCharsets.UTF_8);
    assertEquals(7, ReviewedPhraseCatalog.reviewedRows(input, review, report).size());
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ReviewedPhraseCatalog.reviewedRows(
                input.replace("PENDING", "REVIEWED"), review, report));
    assertEquals(
        ReviewedPhraseCatalog.publicationRows(),
        ReviewedPhraseCatalog.reviewedRows(input.replace("待审夹具", "改动夹具"), review, report));
  }

  @Test
  void pendingSourceAndFormsStillRequireTheSameLicenseAndNormalizedScope() throws Exception {
    var input = text("reviewed-phrases.tsv");
    for (var bad :
        List.of(
            input.replace("nerve fibers", "nerve fibre"),
            input.replace("fatty substances", "fatty substance called"),
            input.replace(
                "https://training.seer.cancer.gov/anatomy/nervous/tissue.html",
                "https://example.invalid/facts"),
            input.replace(
                "https://training.seer.cancer.gov/anatomy/nervous/tissue.html",
                "https://user@training.seer.cancer.gov/anatomy/nervous/tissue.html"),
            input.replace("LicenseRef-NCI-Text-Reuse", "MIT"))) {
      assertThrows(
          IllegalArgumentException.class, () -> ReviewedPhraseCatalog.parseCandidates(bad));
    }
  }

  @Test
  void reviewedOrderAndPresenceRemainBoundDespiteUnreviewedAdditions() throws Exception {
    var input = text("reviewed-phrases.tsv");
    var review = text("reviewed-phrases-review.properties").getBytes(StandardCharsets.UTF_8);
    var report = text("reviewed-phrases-review.md").getBytes(StandardCharsets.UTF_8);
    var lines = withPendingFixture(input).lines().toList();
    var movedPending = new java.util.ArrayList<>(lines);
    movedPending.add(1, movedPending.removeLast());
    assertEquals(
        ReviewedPhraseCatalog.publicationRows(),
        ReviewedPhraseCatalog.reviewedRows(String.join("\n", movedPending) + "\n", review, report));
    var missing = new java.util.ArrayList<>(lines);
    missing.remove(1);
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ReviewedPhraseCatalog.reviewedRows(String.join("\n", missing) + "\n", review, report));
    var reordered = new java.util.ArrayList<>(lines);
    java.util.Collections.swap(reordered, 1, 2);
    assertThrows(
        IllegalArgumentException.class,
        () ->
            ReviewedPhraseCatalog.reviewedRows(
                String.join("\n", reordered) + "\n", review, report));
  }

  private String withPendingFixture(String input) {
    var cells = input.lines().skip(1).findFirst().orElseThrow().split("\\t", -1);
    cells[0] = "fixture tissue";
    cells[1] = "待审夹具";
    cells[2] = "fixture tissues";
    cells[8] = "PENDING";
    return input + String.join("\t", cells) + "\n";
  }

  private String text(String name) throws Exception {
    try (var in = getClass().getResourceAsStream("/lexicon/" + name)) {
      return new String(in.readAllBytes(), StandardCharsets.UTF_8);
    }
  }
}
