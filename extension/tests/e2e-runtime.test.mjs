import assert from "node:assert/strict";
import test from "node:test";
import { EventEmitter } from "node:events";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createApiLaunchSpec, createIncrementalFetch, stopApi } from "./e2e-runtime.mjs";

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

function fakeApi(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const child = new EventEmitter();
  child.pid = 12345;
  child.exitCode = null;
  const signals = [];
  t.mock.method(process, "kill", (pid, signal) => {
    assert.equal(pid, -child.pid);
    signals.push(signal);
  });
  const setTimer = t.mock.method(globalThis, "setTimeout");
  const clearTimer = t.mock.method(globalThis, "clearTimeout");
  return { child, signals, setTimer, clearTimer };
}

function assertDeadlineCleared(setTimer, clearTimer) {
  assert.equal(setTimer.mock.callCount(), 1);
  assert.equal(setTimer.mock.calls[0].arguments[1], 10_000, "TERM grace remains ten seconds");
  assert.equal(clearTimer.mock.callCount(), 1);
  assert.equal(clearTimer.mock.calls[0].arguments[0], setTimer.mock.calls[0].result);
}

test("API stop clears losing timer after graceful close without changing TERM grace", async t => {
  const { child, signals, setTimer, clearTimer } = fakeApi(t);
  const stopped = stopApi(child);
  assert.deepEqual(signals, ["SIGTERM"]);
  child.exitCode = 0;
  child.emit("close", 0, null);
  await stopped;
  assertDeadlineCleared(setTimer, clearTimer);
  t.mock.timers.tick(10_000);
  assert.deepEqual(signals, ["SIGTERM"]);
});

test("API stop retains full grace and awaits close after KILL", async t => {
  const { child, signals, setTimer, clearTimer } = fakeApi(t);
  let completed = false;
  const stopped = stopApi(child).then(() => { completed = true; });
  t.mock.timers.tick(9_999);
  await Promise.resolve();
  assert.deepEqual(signals, ["SIGTERM"]);
  t.mock.timers.tick(1);
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(signals, ["SIGTERM", "SIGKILL"]);
  assert.equal(completed, false, "KILL is not equivalent to child close");
  child.emit("close", null, "SIGKILL");
  await stopped;
  assertDeadlineCleared(setTimer, clearTimer);
});

test("API stop clears timer and propagates child error", async t => {
  const { child, signals, setTimer, clearTimer } = fakeApi(t);
  const error = new Error("synthetic-child-error");
  const stopped = stopApi(child);
  child.emit("error", error);
  await assert.rejects(stopped, error);
  assertDeadlineCleared(setTimer, clearTimer);
  assert.deepEqual(signals, ["SIGTERM"]);
});

test("API stop leaves an already exited child alone", async t => {
  const { child, signals, setTimer, clearTimer } = fakeApi(t);
  child.exitCode = 0;
  await stopApi(child);
  assert.deepEqual(signals, []);
  assert.equal(setTimer.mock.callCount(), 0);
  assert.equal(clearTimer.mock.callCount(), 0);
});

test("graceful API stop does not retain a timer in a real Node process", async () => {
  const source = `import {stopApi} from ${JSON.stringify(new URL('./e2e-runtime.mjs', import.meta.url).href)};
import {EventEmitter} from 'node:events';
const child=new EventEmitter(); child.exitCode=null; child.pid=1;
process.kill=()=>setImmediate(()=>{child.exitCode=0;child.emit('close',0,null);});
await stopApi(child);
console.log(JSON.stringify({timers:process.getActiveResourcesInfo().filter(x=>x==='Timeout').length}));`;
  // This real-process deadline only guards this regression; it does not shorten the API grace.
  const { stdout, stderr } = await promisify(execFile)(process.execPath,
    ["--input-type=module", "-e", source], { timeout: 5_000 });
  assert.deepEqual(JSON.parse(stdout), { timers: 0 });
  assert.equal(stderr, "");
});
