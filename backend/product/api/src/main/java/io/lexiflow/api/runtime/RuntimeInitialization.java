package io.lexiflow.api.runtime;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.core.json.JsonFactory;
import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 核对单容器启动闸门、当次启动证明与数据库实测状态。 */
@Component
public final class RuntimeInitialization {
  private static final Path GATE = Path.of("/run/lexiflow/initializing");
  private static final Path IDENTITY = Path.of("/app/release-identity.json");
  private static final Path PROOF = Path.of("/run/lexiflow/verified.json");
  private static final int MAX_IDENTITY_BYTES = 64 * 1024;
  private static final int MAX_PROOF_BYTES = 8 * 1024;
  private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._+-]{0,127}");
  private static final Pattern SHA256 = Pattern.compile("[a-f0-9]{64}");
  private static final ObjectMapper JSON =
      new ObjectMapper(
          JsonFactory.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build());
  private final Path gate;
  private final Path identity;
  private final Path proof;
  private final boolean distributionEnabled;

  /** 生产使用固定路径；测试可注入隔离路径，但不暴露为用户配置。 */
  public RuntimeInitialization() {
    this(GATE, IDENTITY, PROOF, Boolean.getBoolean("lexiflow.runtime.distribution"));
  }

  RuntimeInitialization(Path gate, Path identity, Path proof, boolean distributionEnabled) {
    this.gate = gate;
    this.identity = identity;
    this.proof = proof;
    this.distributionEnabled = distributionEnabled;
  }

  /**
   * 从精确镜像描述中读取的不可变资料身份。
   *
   * @param releaseId 含义：资料发行标识。取值范围：已核验的非空标识。
   * @param preparationId 含义：资料准备策略标识。取值范围：已核验的非空标识。
   * @param ruleId 含义：提示规则标识。取值范围：已核验的非空标识。
   * @param sqlVersion 含义：数据库结构身份。取值范围：已核验的非空标识。
   * @param sha256 含义：资料包内容摘要。取值范围：小写十六进制六十四位。
   */
  public record PublishedDataset(
      String releaseId, String preparationId, String ruleId, String sqlVersion, String sha256) {}

  /**
   * 合并镜像身份、当次启动证明与数据库实测结果；正式镜像缺少临时证明时绝不发布静态就绪身份。
   *
   * @param releaseImage 含义：是否按正式镜像处理。取值范围：布尔值。
   * @param mode 含义：实际运行模式。取值范围：正式、演示或无效模式的固定标识。
   * @param reason 含义：本次探测原因。取值范围：词库运行原因枚举。
   * @param datasetVersion 含义：数据库实测资料版本。取值范围：非负版本或未知时空值。
   * @param ready 含义：资料是否可安全使用。取值范围：布尔值。
   * @param publishedDataset 含义：已核验的资料身份。取值范围：就绪时为精确身份，否则空值。
   */
  public record Evaluation(
      boolean releaseImage,
      String mode,
      LexiconRuntime.Reason reason,
      Long datasetVersion,
      boolean ready,
      PublishedDataset publishedDataset) {}

  /**
   * 已核验的镜像身份描述。
   *
   * @param softwareVersion 含义：软件版本。取值范围：镜像中的有效非空标识。
   * @param apiContract 含义：接口合同。取值范围：固定的当前接口标识。
   * @param dataset 含义：不可变资料身份。取值范围：已核验的非空身份。
   */
  private record Descriptor(String softwareVersion, String apiContract, PublishedDataset dataset) {}

  /**
   * 当次初始化校验产生的临时证明。
   *
   * @param datasetVersion 含义：校验时读取的数据库版本。取值范围：正整数。
   * @param dataset 含义：已通过校验的资料身份。取值范围：非空身份。
   */
  private record Proof(long datasetVersion, PublishedDataset dataset) {}

  /**
   * 应用启动闸门，将证明中的数据库版本与当前真实探测结果比较。
   *
   * @param state 含义：当前接口与数据库探测状态。取值范围：非空的词库运行状态。
   * @param softwareVersion 含义：应用归档中嵌入的软件版本。取值范围：已校验的非空软件版本。
   * @return 不含本机路径或解析细节的固定安全公开状态。
   */
  public Evaluation evaluate(LexiconRuntime.State state, String softwareVersion) {
    boolean releaseImage = distributionEnabled || existsNoFollow(identity);
    if (existsNoFollow(gate)) {
      return new Evaluation(
          releaseImage,
          state.mode(),
          LexiconRuntime.Reason.INITIALIZING,
          state.version(),
          false,
          null);
    }

    if (!releaseImage) {
      // 普通测试和开发运行保留原有就绪语义，不冒充正式镜像或不可变资料绑定。
      return new Evaluation(
          false, state.mode(), state.reason(), state.version(), state.ready(), null);
    }

    try {
      Descriptor descriptor = readDescriptor(identity);
      Proof proof = readProof(this.proof);
      if (!descriptor.softwareVersion().equals(softwareVersion)
          || !RuntimeStatusController.API_CONTRACT.equals(descriptor.apiContract())
          || !descriptor.dataset().equals(proof.dataset())) {
        return unavailable(state, LexiconRuntime.Reason.IDENTITY_MISMATCH);
      }
      if (!state.ready()
          || state.version() == null
          || state.version() <= 0
          || state.version() != proof.datasetVersion()) {
        return new Evaluation(
            true,
            state.mode(),
            state.reason() == LexiconRuntime.Reason.OK
                    || state.reason() == LexiconRuntime.Reason.PREWARM_DEGRADED
                ? LexiconRuntime.Reason.IDENTITY_MISMATCH
                : state.reason(),
            state.version(),
            false,
            null);
      }
      return new Evaluation(
          true, state.mode(), state.reason(), state.version(), true, descriptor.dataset());
    } catch (IOException | RuntimeException ignored) {
      return unavailable(state, LexiconRuntime.Reason.IDENTITY_MISMATCH);
    }
  }

  private static Evaluation unavailable(LexiconRuntime.State state, LexiconRuntime.Reason reason) {
    return new Evaluation(true, state.mode(), reason, state.version(), false, null);
  }

  private static boolean existsNoFollow(Path path) {
    return Files.exists(path, LinkOption.NOFOLLOW_LINKS);
  }

  private static Descriptor readDescriptor(Path path) throws IOException {
    JsonNode root = readJson(path, MAX_IDENTITY_BYTES);
    requireKeys(
        root,
        Set.of(
            "schemaVersion",
            "softwareVersion",
            "buildIdentity",
            "apiContract",
            "sqlVersion",
            "dataset"));
    if (integer(root, "schemaVersion") != 1) throw invalid();
    String software = string(root, "softwareVersion");
    String contract = string(root, "apiContract");
    String sql = string(root, "sqlVersion");
    if (!IDENTIFIER.matcher(software).matches()
        || !RuntimeStatusController.API_CONTRACT.equals(contract)
        || !IDENTIFIER.matcher(sql).matches()) throw invalid();
    JsonNode build = root.get("buildIdentity");
    if (build == null || !build.isObject() || !software.equals(string(build, "softwareVersion")))
      throw invalid();
    JsonNode dataset = root.get("dataset");
    requireKeys(dataset, Set.of("releaseId", "preparationId", "ruleId", "bytes", "sha256"));
    String release = string(dataset, "releaseId");
    String preparation = string(dataset, "preparationId");
    String rule = string(dataset, "ruleId");
    long bytes = integer(dataset, "bytes");
    String sha = string(dataset, "sha256");
    if (!IDENTIFIER.matcher(release).matches()
        || !IDENTIFIER.matcher(preparation).matches()
        || !IDENTIFIER.matcher(rule).matches()
        || bytes <= 0
        || !SHA256.matcher(sha).matches()) throw invalid();
    return new Descriptor(
        software, contract, new PublishedDataset(release, preparation, rule, sql, sha));
  }

  private static Proof readProof(Path path) throws IOException {
    JsonNode proof = readJson(path, MAX_PROOF_BYTES);
    requireKeys(
        proof,
        Set.of(
            "schemaVersion",
            "cliResult",
            "datasetVersion",
            "releaseId",
            "preparationId",
            "ruleId",
            "sqlVersion",
            "sha256"));
    if (integer(proof, "schemaVersion") != 1 || !"PASS".equals(string(proof, "cliResult")))
      throw invalid();
    long version = integer(proof, "datasetVersion");
    if (version <= 0) throw invalid();
    PublishedDataset dataset =
        new PublishedDataset(
            string(proof, "releaseId"),
            string(proof, "preparationId"),
            string(proof, "ruleId"),
            string(proof, "sqlVersion"),
            string(proof, "sha256"));
    if (!IDENTIFIER.matcher(dataset.releaseId()).matches()
        || !IDENTIFIER.matcher(dataset.preparationId()).matches()
        || !IDENTIFIER.matcher(dataset.ruleId()).matches()
        || !IDENTIFIER.matcher(dataset.sqlVersion()).matches()
        || !SHA256.matcher(dataset.sha256()).matches()) throw invalid();
    return new Proof(version, dataset);
  }

  private static JsonNode readJson(Path path, int maxBytes) throws IOException {
    if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(path))
      throw invalid();
    long size = Files.size(path);
    if (size < 2 || size > maxBytes) throw invalid();
    return JSON.reader()
        .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
        .readTree(Files.readAllBytes(path));
  }

  private static void requireKeys(JsonNode value, Set<String> expected) {
    if (value == null || !value.isObject() || value.size() != expected.size()) throw invalid();
    var fields = value.propertyNames();
    if (fields.size() != expected.size() || !expected.equals(Set.copyOf(fields))) throw invalid();
  }

  private static String string(JsonNode object, String key) {
    JsonNode value = object.get(key);
    if (value == null || !value.isString()) throw invalid();
    return value.asString();
  }

  private static long integer(JsonNode object, String key) {
    JsonNode value = object.get(key);
    if (value == null || !value.isIntegralNumber() || !value.canConvertToLong()) throw invalid();
    return value.asLong();
  }

  private static IllegalArgumentException invalid() {
    return new IllegalArgumentException("runtime identity unavailable");
  }
}
