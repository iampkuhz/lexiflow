import { spawn } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

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
  const args = options.args ?? ['--test', '--test-reporter=tap', path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/version.test.mjs')];
  const timeoutMs = options.timeoutMs ?? 30_000;
  const maxOutputBytes = options.maxOutputBytes ?? 100_000;
  return new Promise((resolve) => {
    let child;
    try { child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] }); }
    catch { resolve(failed('test process could not start', 1)); return; }
    let stdout = ''; let stderr = ''; let bytes = 0; let overflow = false; let timedOut = false; let settled = false;
    const collect = (stream, chunk) => {
      const available = Math.max(0, maxOutputBytes - bytes);
      const bounded = chunk.subarray(0, available);
      bytes += bounded.length;
      if (stream === 'out') stdout += bounded.toString('utf8'); else stderr += bounded.toString('utf8');
      if (bounded.length < chunk.length) { overflow = true; child.kill('SIGKILL'); }
    };
    child.stdout.on('data', (chunk) => collect('out', chunk));
    child.stderr.on('data', (chunk) => collect('err', chunk));
    const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, timeoutMs);
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (stderr) process.stderr.write(stderr);
      if (result.status !== 'PASS' && stdout) process.stderr.write(`TAP output (bounded):\n${stdout}`);
      resolve(result);
    };
    child.on('error', () => finish(failed('test process could not start', 1)));
    child.on('close', (code, signal) => finish(timedOut ? failed('test process timed out', 1) : overflow ? failed('test process output exceeded limit', 1) : signal ? failed('test process cancelled', 1) : evaluateTapSummary(stdout, code ?? 1)));
  });
}

function invokedDirectly() {
  try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (invokedDirectly()) runTests().then((result) => { console.log(JSON.stringify(result)); if (result.status !== 'PASS') process.exitCode = 1; });
