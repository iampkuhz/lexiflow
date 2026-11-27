package io.lexiflow.lexicon.platform.importer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import io.lexiflow.lexicon.platform.persistence.DatasetPackageCodec;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetExport;
import io.lexiflow.lexicon.platform.persistence.SqlitePersistence;
import io.lexiflow.lexicon.platform.persistence.SqliteSchemaInitializer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

@Tag("sqlite")
class ReviewedPhrasePublicationTest {
  @TempDir Path temporary;

  @Test
  void javaPublicationUsesReviewedDefaultAndExportsOnlySourceBoundReviewedPhrases()
      throws Exception {
    temporary = temporary.toRealPath();
    Path source = temporary.resolve("source.csv"), database = temporary.resolve("lexicon.db");
    Files.writeString(
        source,
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
            + "plane,,fixture,\"n. 平面, 扁平物, 机翼, 飞机, 水准, 地位\\na. 平的, 平面的\\nvt. 将...刨平, 刨平, 掠过水面\\nvi. 翱翔, 乘飞机旅行, 刨掉\",,,0,cet6,2784,9000,s:planes/i:planing/p:planed/d:planed/3:planes,,\n"
            + "tissue,,fixture,\"n. 薄的织物, 薄纱, 棉纸, 组织, 一套\\n[化] 组织\",,,0,cet6,2784,9000,s:tissues/i:tissuing/p:tissued/3:tissues/d:tissued,,\n"
            + "equal,,fixture,\"n. 对手, 匹敌, 同辈\\na. 相等的, 平等的, 胜任的, 合适的, 平静的, 不相上下的\\nvt. 等于, 比得上\\n[计] 等长度编码\",,,0,cet6,2784,9000,3:equals/s:equals/d:equalled/p:equalled/i:equalling,,\n"
            + "nerve,,fixture,\"n. 精神, 勇气, 叶脉, 神经\\nvt. 鼓起勇气\",,,0,cet6,2784,9000,s:nerves/p:nerved/i:nerving/d:nerved/3:nerves,,\n"
            + "mostly,,fixture,\"adv. 大概, 大部分, 主要\\n[经] 大部份\",,,0,cet6,2784,9000,,,\n"
            + "cook,,fixture,\"n. 厨子, 厨师\\nvt. 烹调, 煮饭, 加热\\nvi. 在煮着\",,,0,cet6,2784,9000,i:cooking/d:cooked/p:cooked/s:cooks/3:cooks,,\n"
            + "wrap,,fixture,\"n. 外套, 围巾, 包裹物, 限制, 约束, 秘密, 换行\\nvt. 包装, 卷, 缠绕, 包, 裹, 覆盖, 遮蔽, 隐藏, 掩护\\nvi. 缠绕, 穿外衣, 包起来\\n[计] 换行\",,,0,cet6,2784,9000,d:wrapped/p:wrapped/i:wrapping/s:wraps/3:wraps,,\n"
            + "spinal,,fixture,\"a. 针的, 尖刺的, 尖刺状突起的, 脊骨的\\n[医] 棘折; 脊柱的\",,,0,cet6,2784,9000,,,\n"
            + "efficiently,,fixture,\"adv. 生效, 能胜任, 有能力, 效率高, 有效\",,,0,cet6,2784,9000,,,\n"
            + "ease,,fixture,\"n. 安乐, 安逸, 悠闲\\nvt. 使安乐, 使安心, 减轻, 放松\\nvi. 减轻, 放松, 灵活地移动\",,,0,cet6,2784,9000,p:eased/i:easing/d:eased/3:eases,,\n"
            + "attribute,,fixture,\"n. 属性, 标志, 定语\\nvt. 把...归于, 认为...属于\\n[计] 属性\",,,0,cet6,2784,9000,d:attributed/s:attributes/p:attributed/3:attributes/i:attributing,,\n");
    String url = "jdbc:sqlite:" + database;
    Path schema = Path.of(System.getProperty("lexiflow.sqlite.schema.file"));
    SqliteSchemaInitializer.initialize(url, schema);
    LexiconImportMain.execute(
        new String[] {
          "publish",
          "--input",
          source.toString(),
          "--database-url",
          url,
          "--batch-source-id",
          "fixture",
          "--batch-license-id",
          "MIT"
        },
        ignored -> {});
    try (var connection = SqlitePersistence.connect(url);
        var stmt = connection.createStatement()) {
      try (var rows =
          stmt.executeQuery(
              "SELECT gloss, bnc_rank, frequency_rank FROM lexicon_entry WHERE lemma='tissue'")) {
        rows.next();
        assertEquals("组织", rows.getString(1));
        assertEquals(2784, rows.getLong(2));
        assertEquals(10, rows.getLong(3));
      }
      try (var rows =
          stmt.executeQuery(
              "SELECT count(*) FROM lexicon_entry WHERE lemma IN ('neural tissue','fatty tissue','net force','nerve cell','spinal cord','nerve fiber','fatty substance') AND frequency_rank IS NULL AND priority=0")) {
        rows.next();
        assertEquals(7, rows.getInt(1));
      }
      try (var rows =
          stmt.executeQuery(
              "SELECT count(*) FROM lexicon_entry WHERE lemma IN ('nerve fiber','fatty substance')")) {
        rows.next();
        assertEquals(2, rows.getInt(1));
      }
      for (var row : ReviewedPhraseCatalog.publicationRows()) {
        try (var query =
            connection.prepareStatement("SELECT gloss FROM lexicon_entry WHERE lemma=?")) {
          query.setString(1, row.lemma());
          try (var found = query.executeQuery()) {
            org.junit.jupiter.api.Assertions.assertTrue(found.next());
            assertEquals(row.chineseGloss(), found.getString(1));
          }
        }
        try (var query =
            connection.prepareStatement(
                "SELECT json_extract(value,'$.source_digest') FROM lexicon_dataset,json_each(source_manifest) WHERE json_extract(value,'$.source_id')=?")) {
          query.setString(1, row.dictionary().sourceId());
          try (var found = query.executeQuery()) {
            org.junit.jupiter.api.Assertions.assertTrue(found.next());
            assertEquals(
                ReviewedPhraseCatalog.sources().stream()
                    .filter(sourceRow -> sourceRow.sourceId().equals(row.dictionary().sourceId()))
                    .findFirst()
                    .orElseThrow()
                    .sourceDigest(),
                found.getString(1));
          }
        }
      }
      try (var persistence = SqlitePersistence.open(url)) {
        var imported = new java.util.ArrayList<StardictCsvReader.SourceRecord>();
        new StardictCsvReader().read(source, imported::add);
        for (var pair :
            List.of(
                List.of("plane", "飞机"),
                List.of("tissue", "组织"),
                List.of("equal", "相等的"),
                List.of("nerve", "神经"),
                List.of("mostly", "大部分"),
                List.of("cook", "烹调"),
                List.of("wrap", "包"),
                List.of("spinal", "脊柱的"),
                List.of("efficiently", "效率高"),
                List.of("ease", "减轻"),
                List.of("attribute", "归因于"))) {
          var importedRow =
              imported.stream()
                  .map(StardictCsvReader.SourceRecord::row)
                  .filter(r -> r.lemma().equals(pair.getFirst()))
                  .findFirst()
                  .orElseThrow();
          var forms = new java.util.ArrayList<>(importedRow.inflections());
          forms.add(importedRow.lemma());
          for (var form : forms) {
            var actual =
                persistence
                    .repository()
                    .findByForms(persistence.repository().publishedVersion(), List.of(form))
                    .stream()
                    .filter(c -> c.entryId() == LexiconIdentity.entryId(pair.getFirst()))
                    .findFirst()
                    .orElseThrow(
                        () ->
                            new AssertionError(
                                "missing published form: " + pair.getFirst() + " / " + form));
            assertEquals(form, actual.normalizedForm());
            assertEquals(pair.getLast(), actual.finalGloss());
            assertEquals(2784L, actual.bncRank());
            var rankedLemmas =
                List.of(
                    "attribute",
                    "cook",
                    "ease",
                    "efficiently",
                    "equal",
                    "mostly",
                    "nerve",
                    "plane",
                    "spinal",
                    "tissue",
                    "wrap");
            assertEquals((long) rankedLemmas.indexOf(pair.getFirst()) + 1, actual.frequencyRank());
          }
        }

        for (var row : ReviewedPhraseCatalog.publicationRows()) {
          for (String form : List.of(row.lemma(), row.inflections().getFirst())) {
            var forms = new java.util.ArrayList<>(List.of(form.split(" ")));
            forms.add(form);
            var candidates =
                persistence
                    .repository()
                    .findByForms(persistence.repository().publishedVersion(), forms);
            var actual =
                new io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy()
                    .evaluateSelection(form, 0, form.length(), 0, candidates)
                    .hints();
            assertEquals(
                List.of(row.chineseGloss()),
                actual.stream().map(hint -> hint.chineseGloss()).toList());
          }
        }
      }
      String approvalJson;
      try (var rows =
          stmt.executeQuery("SELECT json_array_length(source_manifest) FROM lexicon_dataset")) {
        rows.next();
        assertEquals(5, rows.getInt(1));
      }
      try (var rows =
          stmt.executeQuery(
              "SELECT json_object('schemaVersion',1,'sources',json_group_array(json_object("
                  + "'sourceId',json_extract(value,'$.source_id'),"
                  + "'sourceDigest',json_extract(value,'$.source_digest'),"
                  + "'licenseId',json_extract(value,'$.license_id'),"
                  + "'redistributionApproved',json('true'),"
                  + "'evidenceUrl','https://example.invalid/fixture-only'))) "
                  + "FROM lexicon_dataset,json_each(source_manifest)")) {
        rows.next();
        approvalJson = rows.getString(1);
      }
      Path approvalFile = temporary.resolve("approval.json");
      Files.writeString(approvalFile, approvalJson);
      Path output = temporary.resolve("dataset.zip");
      var exported =
          SqliteDatasetExport.export(connection, output, approvalFile, Files.readAllBytes(schema));
      DatasetPackageCodec.verify(output, exported.sha256(), Files.readAllBytes(schema));
      try (var query = connection.prepareStatement("SELECT json_remove(?, '$.sources[1]')")) {
        query.setString(1, approvalJson);
        try (var rows = query.executeQuery()) {
          rows.next();
          Files.writeString(approvalFile, rows.getString(1));
        }
      }
      assertThrows(
          Exception.class,
          () ->
              SqliteDatasetExport.export(
                  connection,
                  temporary.resolve("must-not-export.zip"),
                  approvalFile,
                  Files.readAllBytes(schema)));
    }
  }

  @Test
  void sourceQualityAndWholeWordHintsSurviveNormalJavaPublication() throws Exception {
    temporary = temporary.toRealPath();
    Path source = temporary.resolve("quality.csv"), database = temporary.resolve("quality.db");
    try (var in = getClass().getResourceAsStream("/lexicon/source-quality-fixture.csv")) {
      Files.copy(in, source);
    }
    String url = "jdbc:sqlite:" + database;
    SqliteSchemaInitializer.initialize(
        url, Path.of(System.getProperty("lexiflow.sqlite.schema.file")));
    LexiconImportMain.execute(
        new String[] {
          "publish",
          "--input",
          source.toString(),
          "--database-url",
          url,
          "--batch-source-id",
          "fixture",
          "--batch-license-id",
          "MIT"
        },
        ignored -> {});
    try (var persistence = SqlitePersistence.open(url)) {
      var repository = persistence.repository();
      var version = repository.publishedVersion();
      for (var pair :
          List.of(
              List.of("that's", "那是"),
              List.of("whisk", "搅拌"),
              List.of("horizontal", "水平的"),
              List.of("crash", "猛撞"),
              List.of("anglican", "圣公会的"),
              List.of("ease", "减轻"),
              List.of("marbled", "有大理石纹的"),
              List.of("in addition", "另外"),
              List.of("swirl", "旋转"),
              List.of("crack", "裂开或使裂开"),
              List.of("startle", "使吃惊"),
              List.of("butter", "黄油"),
              List.of("knob of butter", "一小块黄油"),
              List.of("splash of", "少量"),
              List.of("with ease", "轻松地"),
              List.of("at ease", "自在"),
              List.of("awful lot", "大量"),
              List.of("non-stick", "不粘锅底的"))) {
        var found = repository.findByForms(version, List.of(pair.getFirst()));
        assertEquals(1, found.size(), pair.getFirst());
        assertEquals(pair.getFirst(), found.getFirst().normalizedForm());
        assertEquals(pair.getLast(), found.getFirst().finalGloss());
      }
      var policy = new io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy();
      for (String caption :
          List.of(
              "🤖 ‘non-stick’, marbled! In addition.", "🤖 ‘non‑stick’, marbled! In addition.")) {
        var forms = io.lexiflow.enrichment.application.caption.CandidateForms.fromCaption(caption);
        var actual =
            policy
                .evaluateSelection(
                    caption, 0, caption.length(), 0, repository.findByForms(version, forms))
                .hints();
        assertEquals(
            List.of("不粘锅底的", "有大理石纹的", "另外"), actual.stream().map(h -> h.chineseGloss()).toList());
        assertEquals(List.of(4, 16, 25), actual.stream().map(h -> h.startOffset()).toList());
      }
    }
  }
}
