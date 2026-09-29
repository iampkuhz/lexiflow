import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, unlinkSync, copyFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { parseVersion, readVersion, checkReleaseSource } from '../version.mjs';
import { evaluateTapSummary, runTests } from '../check.mjs';

const git = (cwd, ...args) => execFileSync('git', args, { cwd, stdio: 'ignore', timeout: 10_000 });
function fixture({ head = true, ignore = false } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-release-'));
  git(root, 'init', '-q'); git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
  mkdirSync(path.join(root, 'ops/release'), { recursive: true });
  writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  writeFileSync(path.join(root, 'source'), 'fixture');
  if (ignore) writeFileSync(path.join(root, '.gitignore'), 'ignored/\n');
  if (head) { git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture'); }
  return root;
}
const withFixture = (options, body) => { const root = fixture(options); try { body(root); } finally { rmSync(root, { recursive: true, force: true }); } };
const summary = (n = 1) => `TAP version 13\n${Array.from({ length: n }, (_, i) => `ok ${i + 1} - valid`).join('\n')}\n1..${n}\n# tests ${n}\n# pass ${n}\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n`;

test('version contract accepts boundaries and one final newline only', () => {
  for (const value of ['0.1.0', '65535.2.3\n', '1.2.3\r\n']) assert.ok(parseVersion(value));
  for (const value of ['', '0.0.0', '01.2.3', '1.02.3', '1.2.65536', '99999.2.3', '1.2.3\n\n', '1.2.3\r', ' 1.2.3', '1.2.3 ', '1.2.3x', '\uFEFF1.2.3', '１.2.3']) assert.throws(() => parseVersion(value));
});
test('readVersion uses requested file and rejects missing source', async () => {
  const root = fixture();
  try {
    assert.equal(await readVersion(path.join(root, 'ops/release/version.txt')), '1.2.3');
    await assert.rejects(readVersion(path.join(root, 'absent')));
  } finally { rmSync(root, { recursive: true, force: true }); }
});
test('release source accepts clean root and ignored local output', () => withFixture({ ignore: true }, (root) => {
  mkdirSync(path.join(root, 'ignored')); writeFileSync(path.join(root, 'ignored/private'), 'x');
  assert.equal(checkReleaseSource(root).softwareVersion, '1.2.3');
  assert.match(checkReleaseSource(root).sourceCommit, /^[0-9a-f]{40,64}$/);
}));
for (const [name, mutate] of [
  ['staged edit', (root) => { writeFileSync(path.join(root, 'source'), 'x'); git(root, 'add', 'source'); }],
  ['unstaged edit', (root) => writeFileSync(path.join(root, 'source'), 'x')],
  ['untracked file', (root) => writeFileSync(path.join(root, 'new-file'), 'x')],
  ['deleted file', (root) => unlinkSync(path.join(root, 'source'))],
  ['missing version', (root) => unlinkSync(path.join(root, 'ops/release/version.txt'))],
  ['bad version', (root) => writeFileSync(path.join(root, 'ops/release/version.txt'), '0.0.0')],
]) test(`release source rejects ${name}`, () => withFixture({}, (root) => { mutate(root); assert.throws(() => checkReleaseSource(root)); }));
test('release source rejects subdirectory, empty repository and nonrepository', () => {
  withFixture({}, (root) => assert.throws(() => checkReleaseSource(path.join(root, 'ops'))));
  withFixture({ head: false }, (root) => assert.throws(() => checkReleaseSource(root)));
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-no-git-'));
  try { assert.throws(() => checkReleaseSource(root)); } finally { rmSync(root, { recursive: true, force: true }); }
});
test('release source rejects missing Git executable with fixed reason', () => withFixture({}, (root) => {
  const script = `import {checkReleaseSource} from ${JSON.stringify(new URL('../version.mjs', import.meta.url).href)}; try { checkReleaseSource(process.argv[1]); process.exit(2); } catch (error) { process.stdout.write(error.message); }`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script, root], { encoding: 'utf8', env: { ...process.env, PATH: '' } });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, 'release input is not a Git repository');
  assert.equal(result.stderr, '');
}));
test('version CLI rejects invalid arguments and dirty release without leaking paths', () => withFixture({}, (root) => {
  const cli = path.resolve('ops/release/version.mjs');
  const invalid = spawnSync(process.execPath, [cli, '--unknown'], { encoding: 'utf8' });
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.equal(invalid.stderr, 'release input rejected\n');
  writeFileSync(path.join(root, 'new-file'), 'secret');
  assert.throws(() => checkReleaseSource(root), (error) => !error.message.includes(root) && !error.message.includes('new-file'));
}));
test('release CLI qualifies its own clean source and rejects dirty source', () => withFixture({}, (root) => {
  const cli = path.join(root, 'ops/release/version.mjs');
  copyFileSync(new URL('../version.mjs', import.meta.url), cli);
  git(root, 'add', 'ops/release/version.mjs');
  git(root, 'commit', '-qm', 'synthetic CLI');
  const clean = spawnSync(process.execPath, [cli, '--release'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(clean.status, 0, clean.stderr);
  assert.equal(clean.stderr, '');
  const identity = JSON.parse(clean.stdout);
  assert.equal(identity.softwareVersion, '1.2.3');
  assert.equal(identity.sourceCommit, execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim());
  writeFileSync(path.join(root, 'sensitive-filename'), 'synthetic');
  const dirty = spawnSync(process.execPath, [cli, '--release'], { encoding: 'utf8', timeout: 10000 });
  assert.equal(dirty.status, 1);
  assert.equal(dirty.stdout, '');
  assert.equal(dirty.stderr, 'release input rejected\n');
}));
test('TAP parser requires all unique safe fields, plan, cases and exit zero', () => {
  assert.deepEqual(evaluateTapSummary(summary()), { status: 'PASS', checks_run: 1, failures: 0, errors: 0, skipped: 0, reason: '' });
  const bad = ['', summary() + summary(), summary().replace('1..1', ''), summary().replace('1..1', '1..0'), summary().replace('1..1', '1..2'), summary().replace('1..1', '1..9007199254740993'), summary().replace('# pass 1', ''), summary().replace('# pass 1', '# pass 1\n# pass 1'), summary().replace('# pass 1', '# pass x'), summary().replace('# pass 1', '# pass 9007199254740993'), summary().replace('ok 1 - valid', 'not ok 1 - valid'), summary().replace('ok 1 - valid', ''), summary().replace('# skipped 0', '# skipped 1'), summary().replace('# todo 0', '# todo 1'), summary().replace('# cancelled 0', '# cancelled 1'), summary().replace('# tests 1', '# tests 0')];
  for (const output of bad) assert.equal(evaluateTapSummary(output).status, 'FAIL');
  assert.equal(evaluateTapSummary(summary(), 1).status, 'FAIL');
});
test('runner handles exit failure, timeout, overflow and spawn error', async () => {
  for (const options of [
    { args: ['-e', 'process.exit(1)'] },
    { args: ['-e', 'setTimeout(() => {}, 10000)'], timeoutMs: 30 },
    { args: ['-e', 'process.stdout.write("x".repeat(10000))'], maxOutputBytes: 100 },
    { command: '/not/a/real/runner', args: [] },
  ]) assert.equal((await runTests(options)).status, 'FAIL');
});
