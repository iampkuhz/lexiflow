import assert from "node:assert/strict";
import test from "node:test";
import { createApiLaunchSpec, createIncrementalFetch } from "./e2e-runtime.mjs";

test("API launch spec strips inherited application and Java injection configuration", () => {
  const spec = createApiLaunchSpec({
    repositoryRoot: "/repo",
    apiPort: 32123,
    javaHome: "/jdk/25",
    sourceEnv: {
      PATH: "/usr/bin:/bin",
      HOME: "/home/test",
      SPRING_DATASOURCE_URL: "jdbc:must-not-leak",
      JDBC_URL: "jdbc:must-not-leak",
      LEXIFLOW_SEGMENT_LOG_PATH: "/private/real-captions.jsonl",
      JAVA_TOOL_OPTIONS: "-javaagent:/private/agent.jar",
      JDK_JAVA_OPTIONS: "-Dspring.datasource.url=jdbc:private",
      _JAVA_OPTIONS: "-Dlexiflow.runtime.mode=formal",
      JAVA_OPTS: "-Dspring.datasource.url=jdbc:private",
      GRADLE_OPTS: "-Dspring.datasource.url=jdbc:private"
    }
  });

  assert.equal(spec.command, "/repo/backend/gradlew");
  assert.deepEqual(spec.args, [
    "-p", "backend", "--no-daemon", ":api:bootRun",
    "--args=--server.address=127.0.0.1 --server.port=32123 --lexiflow.runtime.mode=demo --lexiflow.segment-analysis.enabled=false --lexiflow.segment-analysis.console=false"
  ]);
  assert.deepEqual(spec.env, { PATH: "/usr/bin:/bin", HOME: "/home/test", JAVA_HOME: "/jdk/25" });
  assert.equal(spec.args.some((arg) => arg.includes("java_exec")), false);
});

test("incremental fetch passes status GET unchanged, counts only caption POST, and passes other POST unchanged", async () => {
  const calls = [];
  const requests = [];
  const originalFetch = async (...args) => {
    calls.push(args);
    return { args };
  };
  const wrappedFetch = createIncrementalFetch(originalFetch, requests, 0);
  const getArgs = ["http://127.0.0.1:18080/api/v1/runtime-status", { method: "GET", headers: { accept: "application/json" } }];
  const captionArgs = ["http://127.0.0.1:18080/api/v1/caption-hints", { method: "POST", body: JSON.stringify({ captionTopicKey: "fixture" }) }];
  const otherPostArgs = ["http://127.0.0.1:18080/api/v1/other", { method: "POST", body: "not-json" }];

  await wrappedFetch(...getArgs);
  await wrappedFetch(...captionArgs);
  await wrappedFetch(...otherPostArgs);

  assert.equal(calls.length, 3);
  assert.deepEqual(calls[0], getArgs, "GET status preflight must reach fetch unchanged");
  assert.deepEqual(calls[1], captionArgs);
  assert.deepEqual(calls[2], otherPostArgs, "unrelated POST must reach fetch unchanged without JSON parsing");
  assert.deepEqual(requests, [{ captionTopicKey: "fixture" }]);
});
