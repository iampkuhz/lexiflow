import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { openOwnership, spawnOwned } from './runner-ownership-bootstrap.cjs';

// 仅选择固定检查范围与原有子进程预算；CLI 不接收命令或预算覆盖。
const suites = {
  version: { timeoutMs: 60_000, files: ['version'] },
  build: { timeoutMs: 170_000, files: ['archive-identity', 'embedded-identity', 'build', 'build-execution', 'single-image', 'candidate', 'verified-candidate', 'candidate-proof', 'pipeline', 'workflow', 'synthetic-candidates', 'runner'] },
  manifest: { timeoutMs: 80_000, files: ['manifest'] },
  'runtime-entry': { timeoutMs: 80_000, files: ['runtime-entry'] },
  lifecycle: { timeoutMs: 600_000, files: ['lifecycle'] },
  'lifecycle-install': { timeoutMs: 120_000, files: ['lifecycle-install'] },
};

const fields = ['tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo'];
const failed = (reason, errors = 0) => ({ status: 'FAIL', checks_run: 0, failures: errors ? 0 : 1, errors, skipped: 0, reason });

export function evaluateTapSummary(output, exitCode = 0) {
  if (typeof output !== 'string') return failed('TAP output malformed');
  const lines = output.split(/\r?\n/);
  if (lines.filter((line) => line === 'TAP version 13').length !== 1) return failed('TAP header missing or duplicate');
  if (lines.some((line) => /^\s*(?:Bail out!|not ok \d+\b)/.test(line))) return failed('TAP contains failure');
  const plan = lines.filter((line) => /^1\.\./.test(line));
  if (plan.length !== 1 || !/^1\.\.(?:0|[1-9]\d*)$/.test(plan[0])) return failed('TAP plan missing or malformed');
  const count = Number(plan[0].slice(3));
  if (!Number.isSafeInteger(count) || count <= 0) return failed('TAP plan invalid');
  const values = new Map();
  for (const line of lines) {
    const prefix = /^# (tests|pass|fail|cancelled|skipped|todo)(?:\s|$)/.exec(line);
    if (!prefix) continue;
    const match = /^# (tests|pass|fail|cancelled|skipped|todo) (0|[1-9]\d*)$/.exec(line);
    if (!match || values.has(match[1])) return failed('TAP summary malformed or duplicate');
    const value = Number(match[2]);
    if (!Number.isSafeInteger(value)) return failed('TAP summary invalid');
    values.set(match[1], value);
  }
  if (fields.some((key) => !values.has(key))) return failed('TAP summary incomplete');
  const cases = lines.filter((line) => /^(?:not )?ok \d+ - /.test(line));
  if (cases.length !== count || cases.some((line, index) => !line.startsWith(`ok ${index + 1} - `) || /\s#\s(?:SKIP|TODO)\b/.test(line))) return failed('TAP cases incomplete');
  if (values.get('tests') !== count || values.get('pass') !== count || values.get('fail') !== 0 || values.get('cancelled') !== 0 || values.get('skipped') !== 0 || values.get('todo') !== 0 || exitCode !== 0) return failed('TAP tests failed, skipped, cancelled, or inconsistent');
  return { status: 'PASS', checks_run: count, failures: 0, errors: 0, skipped: 0, reason: '' };
}

export function runTests(options = {}) {
  const command = options.command ?? process.execPath;
  const args = options.args ?? ['--test', '--test-reporter=tap', path.join(path.dirname(fileURLToPath(import.meta.url)), '../tests/version.test.mjs')];
  const timeoutMs = options.timeoutMs ?? 30_000;
  const maxOutputBytes = options.maxOutputBytes ?? 100_000;
  return new Promise((resolve) => {
    let child; let ownership; let cleanup; let spawning; let released = false;
    const acquisition = new AbortController();
    let stdout = ''; let stderr = ''; let bytes = 0; let overflow = false; let timedOut = false; let cancelled = false; let settled = false;
    const stop = () => {
      acquisition.abort();
      if (!released && child?.stdio[3]?.writable) child.stdio[3].write('{"op":"stop"}\n');
      if (!ownership) { acquisition.abort(); return; }
      if (!cleanup) {
        cleanup = ownership.cancel().catch(() => false);
        void cleanup.then(clean => { if (!clean) { child?.stdio[3]?.destroy(); void finish(resultForExit(1)); } });
      }
    };
    const onCancel = () => { cancelled = true; stop(); };
    process.on('SIGTERM', onCancel);
    process.on('SIGINT', onCancel);
    // 包含 relay／guard 建立成本；固定 suite 预算不前移或重置。
    const timer = setTimeout(() => { timedOut = true; stop(); }, timeoutMs);
    const resultForExit = (code, signal) => timedOut ? failed('test process timed out', 1)
      : overflow ? failed('test process output exceeded limit', 1)
      : cancelled || signal ? failed('test process cancelled', 1) : evaluateTapSummary(stdout, code ?? 1);
    const finish = async (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      let clean = false;
      try { clean = ownership ? await (cleanup ??= ownership.finish()) : !child; } catch {}
      if (!clean) {
        if (result.status === 'PASS') result = failed('test process ownership cleanup failed', 1);
        else result = { ...result, cleanup_succeeded: false };
      }
      // dispose 的 EOF 只是意外死亡兜底；不能倒填前述 clean 判据。
      if (ownership) await ownership.dispose();
      if (spawning) await spawning.catch(() => {});
      if (child?.reaperDone) await child.reaperDone;
      process.removeListener('SIGTERM', onCancel);
      process.removeListener('SIGINT', onCancel);
      if (stderr) process.stderr.write(stderr);
      if (result.status !== 'PASS' && stdout) process.stderr.write(`TAP output (bounded):\n${stdout}`);
      resolve(result);
    };
    const collect = (stream, chunk) => {
      const available = Math.max(0, maxOutputBytes - bytes);
      const bounded = chunk.subarray(0, available);
      bytes += bounded.length;
      if (stream === 'out') stdout += bounded.toString('utf8'); else stderr += bounded.toString('utf8');
      if (bounded.length < chunk.length) { overflow = true; stop(); }
    };
    void (async () => {
      // 该私有机制只执行当前 Node；不对任意命令假称 preload 已保护。
      if (realpathSync(command) !== realpathSync(process.execPath)) throw new Error('unsupported test executable');
      ownership = await openOwnership(onCancel, acquisition.signal);
      if (timedOut || cancelled) { await ownership.noSpawn(); await finish(resultForExit(1)); return; }
      child = await (spawning = spawnOwned(command, args, { detached: true, stdio: ['ignore', 'pipe', 'pipe', 'pipe'], env: ownership.env }, acquisition.signal, invokedDirectly()));
      child.stdout.on('data', chunk => collect('out', chunk));
      child.stderr.on('data', chunk => collect('err', chunk));
      // exit 先启动屏障，不能等仍由后代持有的 stdout close 形成死锁。
      child.on('exit', () => { if (!cleanup) cleanup = ownership.finish(); });
      child.on('error', () => {
        void ownership.noSpawn().then(() => finish(failed('test process could not start', 1)), () => finish(failed('test process could not start', 1)));
      });
      child.on('close', (code, signal) => { void finish(resultForExit(code, signal)); });
      if (!child.pid) return;
      const allowed = await ownership.attach(child.pid);
      const guarded = await ownership.ready();
      if (!guarded) { stop(); return; }
      let gateInput = ''; let ready = false;
      child.stdio[3].on('error', () => { stop(); });
      child.stdio[3].on('data', chunk => {
        gateInput += chunk;
        if (gateInput.length > 4096) { stop(); return; }
        while (gateInput.includes('\n')) {
          const end = gateInput.indexOf('\n'); const line = gateInput.slice(0, end); gateInput = gateInput.slice(end + 1);
          if (line !== '{"op":"ready"}' || ready) { stop(); return; }
          ready = true;
          if (allowed && !cancelled && !timedOut) { released = true; child.stdio[3].write('{"op":"go"}\n'); }
          else stop();
        }
      });
      if (allowed && !cancelled && !timedOut) child.stdio[3].write('{"op":"armed"}\n');
      else stop();
    })().catch(async () => {
      if (ownership && !child) { try { await ownership.noSpawn(); } catch {} }
      void finish(timedOut || cancelled ? resultForExit(1) : failed('test process could not start', 1));
    });
  });
}

function invokedDirectly() {
  try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (invokedDirectly()) {
  const argv = process.argv.slice(2);
  const suite = argv.length === 0 ? suites.version
    : argv.length === 2 && argv[0] === '--suite' && argv[1] !== 'version' && Object.hasOwn(suites, argv[1]) ? suites[argv[1]] : null;
  const pending = suite ? runTests({ timeoutMs: suite.timeoutMs,
    args: [...(suite === suites.build ? ['--experimental-test-module-mocks'] : []), '--test', '--test-reporter=tap', ...suite.files.map(name => path.join(path.dirname(fileURLToPath(import.meta.url)), `../tests/${name}.test.mjs`))] })
    : Promise.resolve(failed('invalid arguments'));
  pending.then(result => { console.log(JSON.stringify(result)); if (result.status !== 'PASS') process.exitCode = 1; });
}
