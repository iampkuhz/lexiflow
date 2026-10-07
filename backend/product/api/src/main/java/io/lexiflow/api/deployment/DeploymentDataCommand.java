package io.lexiflow.api.deployment;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.platform.importer.LexiconImportMain;
import io.lexiflow.lexicon.platform.persistence.DatasetContentChecksum;
import io.lexiflow.lexicon.platform.persistence.GenerationIdentityReader;
import io.lexiflow.lexicon.platform.persistence.PostgresDatasetPackage;
import io.lexiflow.lexicon.platform.persistence.PostgresDatasetVerifier;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistence;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DataSourceUtils;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.core.json.JsonFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 一次性部署数据 inspect/apply 接缝；不装配 HTTP 或后台任务。 */
public final class DeploymentDataCommand {
  private static final ObjectMapper JSON =
      new ObjectMapper(
          JsonFactory.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build());
  private static final String DATABASE = "lexiflow";
  private static final String SCHEMA = "lexiflow_release";

  private DeploymentDataCommand() {}

  /**
   * 运行受限部署数据动作并只输出单行固定 JSON。
   *
   * @param args 含义：动作及请求文件路径。取值范围：固定四个 CLI 参数。
   */
  public static int run(String[] args) {
    String status = "FAIL";
    String reason = "REQUEST_INVALID";
    try {
      if (args.length != 4
          || !"--deployment-data".equals(args[0])
          || !("inspect".equals(args[1]) || "apply".equals(args[1]))
          || !"--request".equals(args[2])) throw new IllegalArgumentException();
      Path request = Path.of(args[3]);
      rejectSymlinkPath(request);
      if (!Files.isRegularFile(request, LinkOption.NOFOLLOW_LINKS)
          || Files.size(request) > 64 * 1024) throw new IllegalArgumentException();
      JsonNode root = JSON.readTree(Files.readAllBytes(request));
      validateRequest(root, "apply".equals(args[1]));
      // 在连接 PostgreSQL 前拒绝无效制品与输入；部署计划在旧应用停止前依赖此预检。
      verifyGeneration(root.get("generation"));
      Path input = Path.of(text(root, "inputPath"));
      String inputSha = text(root, "inputSha256");
      if (!input.isAbsolute() || !DatasetSha.sha256(input).equals(inputSha))
        throw new IllegalArgumentException();
      if ("csv".equals(text(root, "inputType"))
          && !inputSha.equals(text(root.get("generation"), "inputSha256")))
        throw new IllegalArgumentException();
      if ("csv".equals(text(root, "inputType"))) {
        LexiconImportMain.validateDeploymentInput(
            input, inputSha, generation(root.get("generation")));
      } else {
        var manifest =
            io.lexiflow.lexicon.platform.persistence.DatasetPackageCodec.verifiedManifest(
                input, inputSha, schemaSql());
        var wanted = generation(root.get("generation"));
        if (!manifest.generationVersion().equals(wanted.generationVersion())
            || !manifest.generationCodeVersion().equals(wanted.codeVersion())
            || !manifest.generationSourceDigest().equals(wanted.sourceDigest())
            || !JSON.readTree(manifest.generationConfigJson())
                .equals(JSON.readTree(wanted.configJson()))
            || !JSON.readTree(manifest.generationInputsJson())
                .equals(JSON.readTree(wanted.inputsJson()))) {
          throw new IllegalArgumentException();
        }
      }
      try (Connection connection = connect()) {
        setTargetSearchPath(connection);
        Target target = target(connection);
        if (target.blockedReason() != null) {
          status = "BLOCKED";
          reason = target.blockedReason();
          System.out.println(result(status, reason));
          return 3;
        }
        Snapshot before =
            requireGeneration(
                inspect(connection, target), text(root.get("generation"), "generationVersion"));
        if (before.needsRebuild() && target.exists()) {
          try {
            rejectCrossSchemaDependencies(connection);
          } catch (IllegalStateException dependency) {
            throw new DeploymentBlockedException("CROSS_SCHEMA_DEPENDENCY", dependency);
          }
        }
        if ("inspect".equals(args[1])) {
          status = "PASS";
          reason = before.reason();
          System.out.println(inspectResult(before));
          return 0;
        }
        String inputType = text(root, "inputType");
        String expected = text(root, "expectedFingerprint");
        if (!expected.equals(before.fingerprint())) {
          status = "BLOCKED";
          reason = "FINGERPRINT_DRIFT";
          System.out.println(result(status, reason));
          return 3;
        }
        if (!before.needsRebuild()) {
          System.out.println(
              "{\"status\":\"PASS\",\"rebuilt\":false,\"generationVersion\":\""
                  + before.generationVersion()
                  + "\",\"datasetVersion\":"
                  + before.datasetVersion()
                  + "}");
          return 0;
        }
        long version =
            apply(connection, inputType, input, inputSha, root.get("generation"), expected);
        status = "PASS";
        reason = "OK";
        System.out.println(
            "{\"status\":\"PASS\",\"rebuilt\":true,\"generationVersion\":\""
                + text(root.get("generation"), "generationVersion")
                + "\",\"datasetVersion\":"
                + version
                + "}");
        return 0;
      }
    } catch (DeploymentBlockedException blocked) {
      status = "BLOCKED";
      reason = blocked.reason();
    } catch (SQLException failure) {
      status = "BLOCKED";
      reason = "DATABASE_UNAVAILABLE";
    } catch (Exception failure) {
      status = "FAIL";
      reason = "REQUEST_OR_DATA_INVALID";
    }
    System.out.println(result(status, reason));
    return "BLOCKED".equals(status) ? 3 : 1;
  }

  private static long apply(
      Connection connection,
      String inputType,
      Path input,
      String inputSha,
      JsonNode generationNode,
      String expectedFingerprint)
      throws Exception {
    var source = new SingleConnectionDataSource(connection, true);
    var manager = new DataSourceTransactionManager(source);
    var transaction = new TransactionTemplate(manager);
    var repository = PostgresPersistence.repository(source);
    Long value =
        transaction.execute(
            status -> {
              try {
                Connection tx = DataSourceUtils.getConnection(source);
                try (var lock = tx.createStatement()) {
                  lock.execute(
                      "SELECT pg_advisory_xact_lock(hashtext(current_database()),hashtext('lexiflow_release'))");
                }
                Target target;
                Snapshot now;
                try {
                  target = target(tx);
                  if (target.exists()) lockTargetTables(tx);
                  now = inspect(tx, target);
                } catch (SQLException unreadable) {
                  throw new DeploymentBlockedException("DATABASE_UNAVAILABLE", unreadable);
                }
                now = requireGeneration(now, text(generationNode, "generationVersion"));
                if (!expectedFingerprint.equals(now.fingerprint()))
                  throw new DeploymentBlockedException("FINGERPRINT_DRIFT");
                try {
                  rejectCrossSchemaDependencies(tx);
                } catch (IllegalStateException blocked) {
                  throw new DeploymentBlockedException("CROSS_SCHEMA_DEPENDENCY", blocked);
                }
                long previous = previousVersion(tx, now);
                try (var setting =
                    tx.prepareStatement("SELECT set_config('lexiflow.previous_version',?,true)")) {
                  setting.setString(1, Long.toString(previous));
                  setting.execute();
                }
                byte[] sql = schemaSql();
                String outside = outsideCatalog(tx);
                try (var ddl = tx.createStatement()) {
                  if (target.exists()) ddl.execute("DROP SCHEMA lexiflow_release CASCADE");
                  if (!outside.equals(outsideCatalog(tx)))
                    throw new DeploymentBlockedException("CROSS_SCHEMA_DEPENDENCY");
                  ddl.execute(
                      "CREATE SCHEMA lexiflow_release AUTHORIZATION \""
                          + target.user().replace("\"", "\"\"")
                          + "\"");
                }
                if ("package".equals(inputType)) {
                  PostgresDatasetPackage.initializeWithinTransaction(
                      tx, input, inputSha, DATABASE, SCHEMA, sql);
                  long packageVersion =
                      scalar(tx, "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id=1");
                  long newVersion =
                      Math.max(packageVersion, Math.max(previous + 1, System.currentTimeMillis()));
                  if (newVersion > 9_007_199_254_740_991L)
                    throw new IllegalStateException("version out of range");
                  try (var update =
                      tx.prepareStatement(
                          "UPDATE lexicon_dataset SET lexicon_version=? WHERE dataset_id=1")) {
                    update.setLong(1, newVersion);
                    update.executeUpdate();
                  }
                } else {
                  try (var statement = tx.createStatement()) {
                    statement.execute(new String(sql, StandardCharsets.UTF_8));
                  }
                  var generation = generation(generationNode);
                  LexiconImportMain.publishOnRepository(
                      input,
                      inputSha,
                      generation.configJson().contains("ecdict-stardict")
                          ? "ecdict-stardict"
                          : "ecdict-stardict",
                      "MIT",
                      Instant.EPOCH,
                      generation,
                      repository);
                }
                Snapshot after = inspect(tx, target(tx));
                if (after.needsRebuild()
                    || after.datasetVersion() == null
                    || !text(generationNode, "generationVersion")
                        .equals(after.generationVersion())) {
                  throw new IllegalStateException("published data validation failed");
                }
                return after.datasetVersion();
              } catch (DeploymentBlockedException failure) {
                statusRollback(status);
                throw failure;
              } catch (Exception failure) {
                statusRollback(status);
                throw new IllegalStateException(failure);
              }
            });
    if (value == null) throw new IllegalStateException("transaction returned no version");
    return value;
  }

  private static void statusRollback(org.springframework.transaction.TransactionStatus status) {
    status.setRollbackOnly();
  }

  private static Snapshot inspect(Connection connection, Target target) throws SQLException {
    if (!target.exists())
      return new Snapshot(
          target.database(),
          SCHEMA,
          fingerprint(connection, target, "missing"),
          true,
          "SCHEMA_MISSING",
          null,
          null);
    String structural = structure(connection);
    String generation = null;
    Long version = null;
    boolean success = false;
    boolean markerPresent =
        tableExists(connection, "lexicon_dataset")
            && scalar(connection, "SELECT COUNT(*) FROM lexiflow_release.lexicon_dataset") > 0;
    if ("valid".equals(structural)) {
      try {
        var publication = PostgresDatasetVerifier.verify(connection);
        generation = publication.generationVersion();
        version = publication.datasetVersion();
        success = true;
      } catch (java.io.IOException invalid) {
        success = false;
      }
    }
    boolean needs = !"valid".equals(structural) || !success;
    String reason =
        needs
            ? ("valid".equals(structural)
                ? (markerPresent ? "RECORD_INVALID" : "RECORD_MISSING")
                : "SCHEMA_INVALID")
            : "OK";
    return new Snapshot(
        target.database(),
        SCHEMA,
        fingerprint(
            connection, target, structural + ":" + generation + ":" + version + ":" + success),
        needs,
        reason,
        generation,
        version);
  }

  private static Snapshot requireGeneration(Snapshot snapshot, String requested) {
    if (!snapshot.needsRebuild() && !requested.equals(snapshot.generationVersion()))
      return new Snapshot(
          snapshot.database(),
          snapshot.schema(),
          snapshot.fingerprint(),
          true,
          "GENERATION_CHANGED",
          snapshot.generationVersion(),
          snapshot.datasetVersion());
    return snapshot;
  }

  private static String structure(Connection connection) throws SQLException {
    long count =
        scalar(
            connection,
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='lexiflow_release' AND table_name IN ('lexicon_dataset','lexicon_entry','lexicon_form') AND table_type='BASE TABLE'");
    if (count != 3) return "invalid";
    long columns =
        scalar(
            connection,
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='lexiflow_release' AND ((table_name='lexicon_dataset' AND column_name IN ('dataset_id','lexicon_version','source_manifest','source_row_count','entry_count','lookup_count','preparation_policy','generation_version','generation_code_version','generation_source_digest','generation_config','generation_inputs','content_sha256','imported_at')) OR (table_name='lexicon_entry' AND column_name IN ('entry_id','lemma','gloss','ranked_word','hint_priority','complex_list_count','cache_priority')) OR (table_name='lexicon_form' AND column_name IN ('normalized_form','entry_id')))");
    if (columns != 23) return "invalid";
    long extraSchemaObjects =
        scalar(
            connection,
            """
        SELECT
          (SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='lexiflow_release' AND t.typrelid=0 AND t.typtype<>'p' AND t.typcategory<>'A')+
          (SELECT COUNT(*) FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_conversion c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_ts_config x JOIN pg_namespace n ON n.oid=x.cfgnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_ts_dict x JOIN pg_namespace n ON n.oid=x.dictnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_ts_parser x JOIN pg_namespace n ON n.oid=x.prsnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_ts_template x JOIN pg_namespace n ON n.oid=x.tmplnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_opclass x JOIN pg_namespace n ON n.oid=x.opcnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_opfamily x JOIN pg_namespace n ON n.oid=x.opfnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_extension x JOIN pg_namespace n ON n.oid=x.extnamespace WHERE n.nspname='lexiflow_release')+
          (SELECT COUNT(*) FROM pg_statistic_ext x JOIN pg_namespace n ON n.oid=x.stxnamespace WHERE n.nspname='lexiflow_release')
        """);
    return extraSchemaObjects == 0 ? "valid" : "invalid";
  }

  private static long scalar(Connection connection, String sql) throws SQLException {
    try (var statement = connection.createStatement();
        var rows = statement.executeQuery(sql)) {
      rows.next();
      return rows.getLong(1);
    }
  }

  private static void rejectCrossSchemaDependencies(Connection connection) throws SQLException {
    // DROP SCHEMA 涵盖任意名称的关系、类型与函数；按被依赖对象所属 schema 判断，
    // 并把 VIEW 规则、FK、默认值、触发器与策略归属映射回其外部关系。
    String sql =
        """
        SELECT COUNT(*) FROM pg_depend d
        CROSS JOIN LATERAL pg_identify_object(d.refclassid,d.refobjid,d.refobjsubid) ref
        CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,d.objsubid) dep
        LEFT JOIN pg_rewrite rw ON d.classid='pg_rewrite'::regclass AND rw.oid=d.objid
        LEFT JOIN pg_class rw_rel ON rw_rel.oid=rw.ev_class
        LEFT JOIN pg_namespace rw_ns ON rw_ns.oid=rw_rel.relnamespace
        LEFT JOIN pg_constraint con ON d.classid='pg_constraint'::regclass AND con.oid=d.objid
        LEFT JOIN pg_class con_rel ON con_rel.oid=con.conrelid
        LEFT JOIN pg_namespace con_ns ON con_ns.oid=con_rel.relnamespace
        LEFT JOIN pg_type con_type ON con_type.oid=con.contypid
        LEFT JOIN pg_namespace con_type_ns ON con_type_ns.oid=con_type.typnamespace
        LEFT JOIN pg_attrdef ad ON d.classid='pg_attrdef'::regclass AND ad.oid=d.objid
        LEFT JOIN pg_class ad_rel ON ad_rel.oid=ad.adrelid
        LEFT JOIN pg_namespace ad_ns ON ad_ns.oid=ad_rel.relnamespace
        LEFT JOIN pg_trigger trg ON d.classid='pg_trigger'::regclass AND trg.oid=d.objid
        LEFT JOIN pg_class trg_rel ON trg_rel.oid=trg.tgrelid
        LEFT JOIN pg_namespace trg_ns ON trg_ns.oid=trg_rel.relnamespace
        LEFT JOIN pg_policy pol ON d.classid='pg_policy'::regclass AND pol.oid=d.objid
        LEFT JOIN pg_class pol_rel ON pol_rel.oid=pol.polrelid
        LEFT JOIN pg_namespace pol_ns ON pol_ns.oid=pol_rel.relnamespace
        WHERE d.deptype NOT IN ('i','e')
        AND (ref.schema='lexiflow_release' OR
          (d.refclassid='pg_namespace'::regclass AND d.refobjid=(
            SELECT oid FROM pg_namespace WHERE nspname='lexiflow_release')))
        AND COALESCE(CASE
          WHEN d.classid='pg_rewrite'::regclass THEN rw_ns.nspname
          WHEN d.classid='pg_constraint'::regclass THEN COALESCE(con_ns.nspname,con_type_ns.nspname)
          WHEN d.classid='pg_attrdef'::regclass THEN ad_ns.nspname
          WHEN d.classid='pg_trigger'::regclass THEN trg_ns.nspname
          WHEN d.classid='pg_policy'::regclass THEN pol_ns.nspname
          ELSE dep.schema END,'')<>'lexiflow_release'
        """;
    if (scalar(connection, sql) != 0) throw new IllegalStateException("cross-schema dependency");
  }

  private static void lockTargetTables(Connection connection) throws SQLException {
    var names = new java.util.ArrayList<String>();
    try (var statement = connection.createStatement();
        var rows =
            statement.executeQuery(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                    + "ON n.oid=c.relnamespace WHERE n.nspname='lexiflow_release' "
                    + "AND c.relkind IN ('r','p','m','f') ORDER BY c.oid")) {
      while (rows.next())
        names.add("lexiflow_release.\"" + rows.getString(1).replace("\"", "\"\"") + "\"");
    }
    if (!names.isEmpty())
      try (var lock = connection.createStatement()) {
        lock.execute("LOCK TABLE " + String.join(",", names) + " IN ACCESS EXCLUSIVE MODE");
      }
  }

  private static long previousVersion(Connection connection, Snapshot snapshot)
      throws SQLException {
    if (snapshot.datasetVersion() != null) return snapshot.datasetVersion();
    if (!tableExists(connection, "lexicon_dataset")
        || scalar(
                connection,
                "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='lexiflow_release' "
                    + "AND table_name='lexicon_dataset' AND column_name='lexicon_version'")
            != 1) return 0;
    return scalar(
        connection,
        "SELECT COALESCE(MAX(lexicon_version),0) FROM lexiflow_release.lexicon_dataset");
  }

  private static String outsideCatalog(Connection connection) throws SQLException {
    var value = new StringBuilder();
    appendQuery(
        connection,
        value,
        "SELECT c.oid::text,c.relname,c.relkind,n.nspname "
            + "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            + "WHERE n.nspname<>'lexiflow_release' AND n.nspname NOT LIKE 'pg_%' "
            + "AND n.nspname<>'information_schema' ORDER BY c.oid");
    appendQuery(
        connection,
        value,
        "SELECT p.oid::text,p.proname,n.nspname "
            + "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            + "WHERE n.nspname<>'lexiflow_release' AND n.nspname NOT LIKE 'pg_%' "
            + "AND n.nspname<>'information_schema' ORDER BY p.oid");
    appendQuery(
        connection,
        value,
        "SELECT t.oid::text,t.typname,n.nspname "
            + "FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            + "WHERE n.nspname<>'lexiflow_release' AND n.nspname NOT LIKE 'pg_%' "
            + "AND n.nspname<>'information_schema' ORDER BY t.oid");
    return value.toString();
  }

  private static Target target(Connection connection) throws SQLException {
    String database, user, server;
    try (var s = connection.createStatement();
        var r =
            s.executeQuery(
                "SELECT current_database(),current_user,current_setting('server_version_num')")) {
      r.next();
      database = r.getString(1);
      user = r.getString(2);
      server = r.getString(3);
    }
    if (!DATABASE.equals(database) || user == null || user.isBlank())
      return new Target(database, user, false, "TARGET_INVALID");
    if (!server.startsWith("17"))
      return new Target(database, user, false, "PG_VERSION_UNSUPPORTED");
    try (var s =
        connection.prepareStatement(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=?")) {
      s.setString(1, SCHEMA);
      try (var r = s.executeQuery()) {
        if (!r.next()) {
          if (scalar(
                  connection,
                  "SELECT has_database_privilege(current_user,current_database(),'CREATE')::integer")
              == 0) return new Target(database, user, false, "AUTHORIZATION_FAILED");
          return new Target(database, user, false, null);
        }
        if (!user.equals(r.getString(1)))
          return new Target(database, user, true, "AUTHORIZATION_FAILED");
      }
    }
    return new Target(database, user, true, null);
  }

  private static String fingerprint(Connection connection, Target target, String detail)
      throws SQLException {
    var input =
        new StringBuilder(target.database())
            .append('\n')
            .append(SCHEMA)
            .append('\n')
            .append(target.user())
            .append('\n')
            .append(detail)
            .append('\n');
    appendQuery(
        connection, input, "SELECT oid::text FROM pg_database WHERE datname=current_database()");
    appendQuery(
        connection,
        input,
        "SELECT oid::text,nspowner::text FROM pg_namespace WHERE nspname='lexiflow_release'");
    appendQuery(
        connection,
        input,
        "SELECT c.relname,c.relkind,c.relowner::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='lexiflow_release' ORDER BY c.relname");
    appendQuery(
        connection,
        input,
        "SELECT table_name,column_name,data_type,udt_name,character_maximum_length,is_nullable,column_default FROM information_schema.columns WHERE table_schema='lexiflow_release' ORDER BY table_name,ordinal_position");
    appendQuery(
        connection,
        input,
        "SELECT c.conname,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='lexiflow_release' ORDER BY c.conname");
    appendQuery(
        connection,
        input,
        "SELECT i.relname,pg_get_indexdef(i.oid) FROM pg_class i JOIN pg_namespace n ON n.oid=i.relnamespace WHERE n.nspname='lexiflow_release' AND i.relkind='i' ORDER BY i.relname");
    appendQuery(
        connection,
        input,
        """
        SELECT d.classid::regclass::text,d.objid::text,d.objsubid,
          d.refclassid::regclass::text,d.refobjid::text,d.refobjsubid,d.deptype
        FROM pg_depend d
        CROSS JOIN LATERAL pg_identify_object(d.refclassid,d.refobjid,d.refobjsubid) ref
        WHERE ref.schema='lexiflow_release' OR
          (d.refclassid='pg_namespace'::regclass AND d.refobjid=(
            SELECT oid FROM pg_namespace WHERE nspname='lexiflow_release'))
        ORDER BY d.classid,d.objid,d.objsubid,d.refclassid,d.refobjid,d.refobjsubid,d.deptype
        """);
    if (tableExists(connection, "lexicon_dataset"))
      appendQuery(
          connection,
          input,
          "SELECT to_jsonb(d)::text FROM lexiflow_release.lexicon_dataset d ORDER BY dataset_id");
    for (String table : List.of("lexicon_dataset", "lexicon_entry", "lexicon_form")) {
      if (tableExists(connection, table))
        input
            .append(table)
            .append('=')
            .append(scalar(connection, "SELECT COUNT(*) FROM lexiflow_release.\"" + table + "\""))
            .append('\n');
      else input.append(table).append("=missing\n");
    }
    // 旧结构仍需能 inspect 并安全给出重建计划；旧列不能用于最新内容摘要。
    if (detail.startsWith("valid:")
        && tableExists(connection, "lexicon_entry")
        && tableExists(connection, "lexicon_form")) {
      input.append("content=").append(DatasetContentChecksum.calculate(connection)).append('\n');
    }
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256")
                  .digest(input.toString().getBytes(StandardCharsets.UTF_8)));
    } catch (Exception impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private static boolean tableExists(Connection connection, String table) throws SQLException {
    try (var s =
        connection.prepareStatement(
            "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='lexiflow_release' AND c.relname=? AND c.relkind IN ('r','p'))")) {
      s.setString(1, table);
      try (var r = s.executeQuery()) {
        r.next();
        return r.getBoolean(1);
      }
    }
  }

  private static void appendQuery(Connection connection, StringBuilder target, String sql)
      throws SQLException {
    try (var s = connection.createStatement();
        var r = s.executeQuery(sql)) {
      var md = r.getMetaData();
      while (r.next()) {
        for (int i = 1; i <= md.getColumnCount(); i++)
          target.append(r.getString(i)).append('\u0001');
        target.append('\n');
      }
    }
  }

  private static void verifyGeneration(JsonNode generation) throws Exception {
    if (!GenerationIdentityReader.embeddedCodeVersion().equals(text(generation, "codeVersion")))
      throw new IllegalArgumentException();
    String input = text(generation, "inputSha256"),
        source = text(generation, "sourceDigest"),
        version = text(generation, "generationVersion");
    JsonNode upstream = generation.get("upstream"), config = generation.get("config");
    if (!"MIT".equals(text(config, "licenseId"))
        || !"ecdict-stardict".equals(text(config, "sourceId")))
      throw new IllegalArgumentException();
    String expectedSource =
        hash(canonical(Map.of("inputSha256", input, "upstream", toMap(upstream))));
    String expectedVersion =
        hash(
            canonical(
                Map.of(
                    "codeVersion",
                    text(generation, "codeVersion"),
                    "sourceDigest",
                    source,
                    "config",
                    toMap(config))));
    if (!source.equals(expectedSource) || !version.equals(expectedVersion))
      throw new IllegalArgumentException();
  }

  private static LexiconImportMetadata.GenerationIdentity generation(JsonNode node)
      throws Exception {
    return new LexiconImportMetadata.GenerationIdentity(
        text(node, "codeVersion"),
        text(node, "sourceDigest"),
        text(node, "generationVersion"),
        text(node, "inputSha256"),
        canonical(toMap(node.get("config"))),
        canonical(
            Map.of(
                "inputSha256",
                text(node, "inputSha256"),
                "upstream",
                toMap(node.get("upstream")))));
  }

  private static Object toMap(JsonNode node) {
    if (node.isObject()) {
      var map = new TreeMap<String, Object>();
      node.properties().forEach(e -> map.put(e.getKey(), toMap(e.getValue())));
      return map;
    }
    if (node.isArray()) {
      var list = new java.util.ArrayList<>();
      node.forEach(v -> list.add(toMap(v)));
      return list;
    }
    if (node.isString()) return node.asString();
    if (node.isBoolean()) return node.asBoolean();
    if (node.isNumber()) return node.numberValue();
    if (node.isNull()) return null;
    throw new IllegalArgumentException();
  }

  private static String canonical(Object value) throws Exception {
    if (value instanceof Map<?, ?> map) {
      var sorted = new TreeMap<String, Object>();
      map.forEach((key, item) -> sorted.put((String) key, canonicalValue(item)));
      return JSON.writeValueAsString(sorted);
    }
    return JSON.writeValueAsString(canonicalValue(value));
  }

  private static Object canonicalValue(Object value) {
    if (value instanceof Map<?, ?> map) {
      var sorted = new TreeMap<String, Object>();
      map.forEach((key, item) -> sorted.put((String) key, canonicalValue(item)));
      return sorted;
    }
    if (value instanceof List<?> list)
      return list.stream().map(DeploymentDataCommand::canonicalValue).toList();
    return value;
  }

  private static String hash(String value) throws Exception {
    return HexFormat.of()
        .formatHex(
            MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
  }

  private static void validateRequest(JsonNode root, boolean apply) {
    exactFields(
        root,
        apply
            ? Set.of("inputType", "inputPath", "inputSha256", "generation", "expectedFingerprint")
            : Set.of("inputType", "inputPath", "inputSha256", "generation"));
    if (!Set.of("csv", "package").contains(text(root, "inputType"))
        || !Path.of(text(root, "inputPath")).isAbsolute()
        || !text(root, "inputSha256").matches("[0-9a-f]{64}")
        || root.get("generation") == null) throw new IllegalArgumentException();
    if (apply && !text(root, "expectedFingerprint").matches("[0-9a-f]{64}"))
      throw new IllegalArgumentException();
    JsonNode generation = root.get("generation");
    exactFields(
        generation,
        Set.of(
            "codeVersion",
            "sourceDigest",
            "generationVersion",
            "inputSha256",
            "config",
            "upstream"));
    for (String key : List.of("codeVersion", "sourceDigest", "generationVersion", "inputSha256"))
      if (!text(generation, key).matches("[0-9a-f]{64}")) throw new IllegalArgumentException();
    exactFields(generation.get("config"), Set.of("sourceId", "licenseId"));
    if (!generation.get("upstream").isObject()) throw new IllegalArgumentException();
  }

  private static void exactFields(JsonNode node, Set<String> expected) {
    if (node == null || !node.isObject()) throw new IllegalArgumentException();
    var fields = new java.util.HashSet<String>();
    node.properties().forEach(entry -> fields.add(entry.getKey()));
    if (!fields.equals(expected)) throw new IllegalArgumentException();
  }

  private static Connection connect() throws Exception {
    String url = System.getenv("LEXIFLOW_DEPLOYMENT_JDBC_URL");
    if (url == null || !validJdbcUrl(url)) throw new IllegalArgumentException();
    String user = System.getenv("LEXIFLOW_DEPLOYMENT_DB_USER"), password = secret();
    if (user == null || user.isBlank() || password == null) throw new IllegalArgumentException();
    var props = new Properties();
    props.setProperty("user", user);
    props.setProperty("password", password);
    Connection connection = DriverManager.getConnection(url, props);
    connection.setSchema(SCHEMA);
    try (var s = connection.createStatement();
        var r = s.executeQuery("SELECT current_user")) {
      r.next();
      if (!user.equals(r.getString(1))) {
        connection.close();
        throw new IllegalArgumentException();
      }
    }
    return connection;
  }

  private static String secret() throws IOException {
    String file = System.getenv("LEXIFLOW_DEPLOYMENT_DB_PASSWORD_FILE");
    if (file != null && !file.isBlank()) {
      Path path = Path.of(file);
      rejectSymlinkPath(path);
      if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.size(path) > 4096)
        throw new IOException();
      try {
        Set<PosixFilePermission> perms = Files.getPosixFilePermissions(path);
        if (perms.stream()
            .anyMatch(p -> p.name().startsWith("GROUP_") || p.name().startsWith("OTHERS_")))
          throw new IOException();
      } catch (UnsupportedOperationException ignored) {
      }
      byte[] bytes;
      try (var channel =
          Files.newByteChannel(
              path, Set.of(java.nio.file.StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS))) {
        bytes = java.nio.channels.Channels.newInputStream(channel).readAllBytes();
      }
      String value =
          StandardCharsets.UTF_8
              .newDecoder()
              .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
              .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT)
              .decode(java.nio.ByteBuffer.wrap(bytes))
              .toString();
      if (value.endsWith("\r\n")) return value.substring(0, value.length() - 2);
      if (value.endsWith("\n")) return value.substring(0, value.length() - 1);
      return value;
    }
    return System.getenv("LEXIFLOW_DEPLOYMENT_DB_PASSWORD");
  }

  private static byte[] schemaSql() throws IOException {
    try (var in =
        DeploymentDataCommand.class.getResourceAsStream("/META-INF/lexiflow-schema.sql")) {
      if (in == null) throw new IOException();
      return in.readAllBytes();
    }
  }

  private static boolean validJdbcUrl(String url) {
    try {
      if (!url.startsWith("jdbc:postgresql://")) return false;
      var uri = java.net.URI.create(url.substring("jdbc:".length()));
      if (uri.getRawUserInfo() != null
          || uri.getFragment() != null
          || !Set.of("127.0.0.1", "localhost", "host.containers.internal", "postgres")
              .contains(uri.getHost())
          || uri.getPort() < 1
          || uri.getPort() > 65535
          || !"/lexiflow".equals(uri.getRawPath())) return false;
      String query = uri.getRawQuery();
      if (query == null) return true;
      if (!"currentSchema=lexiflow_release".equals(query)) return false;
      return true;
    } catch (RuntimeException invalid) {
      return false;
    }
  }

  private static void rejectSymlinkPath(Path path) throws IOException {
    if (path == null || !path.isAbsolute()) throw new IOException();
    Path current = path.getRoot();
    for (Path component : path) {
      current = current.resolve(component);
      if (Files.isSymbolicLink(current)) throw new IOException();
    }
  }

  private static void setTargetSearchPath(Connection connection) throws SQLException {
    try (var s = connection.createStatement()) {
      s.execute("SET search_path TO lexiflow_release");
      // JDBC 默认时区来自 JVM；宿主与容器必须以同一表示冻结 timestamptz 观测。
      s.execute("SET TIME ZONE 'UTC'");
    }
  }

  private static String text(JsonNode node, String key) {
    return text(node == null ? null : node.get(key));
  }

  private static String text(JsonNode node) {
    if (node == null || !node.isString() || node.asString().isBlank())
      throw new IllegalArgumentException();
    return node.asString();
  }

  private static String inspectResult(Snapshot s) {
    return "{\"status\":\"PASS\",\"database\":\""
        + s.database()
        + "\",\"schema\":\""
        + SCHEMA
        + "\",\"fingerprint\":\""
        + s.fingerprint()
        + "\",\"needsRebuild\":"
        + s.needsRebuild()
        + ",\"reason\":\""
        + s.reason()
        + "\",\"generationVersion\":"
        + (s.generationVersion() == null ? "null" : "\"" + s.generationVersion() + "\"")
        + ",\"datasetVersion\":"
        + (s.datasetVersion() == null ? "null" : s.datasetVersion())
        + "}";
  }

  private static String result(String status, String reason) {
    return "{\"status\":\"" + status + "\",\"reason\":\"" + reason + "\"}";
  }

  /**
   * 当前数据库与专用 schema 的受控目标观察。
   *
   * @param database 含义：当前数据库名。取值范围：连接返回值。
   * @param user 含义：当前角色名。取值范围：连接返回值。
   * @param exists 含义：专用 schema 是否存在。取值范围：布尔值。
   * @param blockedReason 含义：不可操作的原因。取值范围：固定枚举或空值。
   */
  private record Target(String database, String user, boolean exists, String blockedReason) {}

  /**
   * 供确认与事务内复核的数据库数据快照。
   *
   * @param database 含义：当前数据库名。取值范围：固定目标。
   * @param schema 含义：当前 schema 名。取值范围：固定目标。
   * @param fingerprint 含义：状态指纹。取值范围：64 位小写十六进制。
   * @param needsRebuild 含义：是否应完整重建。取值范围：布尔值。
   * @param reason 含义：观察原因。取值范围：固定枚举。
   * @param generationVersion 含义：已发布生成身份。取值范围：有效摘要或空值。
   * @param datasetVersion 含义：已发布版本。取值范围：正安全整数或空值。
   */
  private record Snapshot(
      String database,
      String schema,
      String fingerprint,
      boolean needsRebuild,
      String reason,
      String generationVersion,
      Long datasetVersion) {}

  /** 保存固定阻断原因与内部异常链；CLI 仅输出固定原因，不输出异常或连接信息。 */
  private static final class DeploymentBlockedException extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final String reason;

    DeploymentBlockedException(String reason) {
      this(reason, null);
    }

    DeploymentBlockedException(String reason, Throwable cause) {
      super(reason, cause);
      this.reason = reason;
    }

    String reason() {
      return reason;
    }
  }

  /** 按原始字节读取受限输入文件摘要，拒绝路径中的符号链接。 */
  private static final class DatasetSha {
    static String sha256(Path path) throws Exception {
      rejectSymlinkPath(path);
      if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) throw new IOException();
      try (var in = Files.newInputStream(path)) {
        var md = MessageDigest.getInstance("SHA-256");
        byte[] b = new byte[65536];
        for (int n; (n = in.read(b)) >= 0; ) md.update(b, 0, n);
        return HexFormat.of().formatHex(md.digest());
      }
    }
  }
}
