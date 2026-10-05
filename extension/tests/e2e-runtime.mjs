import { resolve } from "node:path";

const SAFE_ENV_KEYS = ["PATH", "HOME", "TMPDIR", "TMP", "TEMP", "USER", "LOGNAME", "LANG"];

/** 为合成 E2E 构造封闭的 Gradle 启动合同，不继承应用配置或 Java 注入项。 */
export function createApiLaunchSpec({ repositoryRoot, apiPort, javaHome, sourceEnv = process.env }) {
  if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) throw new TypeError("apiPort must be a valid TCP port");
  const safeEnv = Object.fromEntries(
    SAFE_ENV_KEYS.flatMap((key) => sourceEnv[key] === undefined ? [] : [[key, sourceEnv[key]]])
  );
  for (const [key, value] of Object.entries(sourceEnv)) {
    if (/^LC_[A-Z0-9_]+$/u.test(key)) safeEnv[key] = value;
  }
  safeEnv.JAVA_HOME = javaHome;

  return {
    command: resolve(repositoryRoot, "backend/gradlew"),
    args: [
      "-p", "backend", "--no-daemon", ":api:bootRun",
      `--args=--server.address=127.0.0.1 --server.port=${apiPort} --lexiflow.runtime.mode=demo --lexiflow.segment-analysis.enabled=false --lexiflow.segment-analysis.console=false`
    ],
    env: safeEnv
  };
}

/** 可序列化到合成 Service Worker 的同一统计/延迟接线，且仅触碰目标字幕 POST。 */
export function createIncrementalFetch(originalFetch, requests, delayMs = 250) {
  const isTargetPost = (input, init = {}) => {
    const url = typeof input === "string" || input instanceof URL
      ? new URL(input)
      : new URL(input.url);
    const method = String(init.method ?? input.method ?? "GET").toUpperCase();
    return method === "POST" && url.pathname === "/api/v1/caption-hints";
  };
  return async (...args) => {
    if (!isTargetPost(args[0], args[1])) return originalFetch(...args);
    requests.push(JSON.parse(args[1].body));
    await new Promise((resolveDelay) => setTimeout(resolveDelay, delayMs));
    return originalFetch(...args);
  };
}
