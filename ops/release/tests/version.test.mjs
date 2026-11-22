import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, unlinkSync, copyFileSync, cpSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import childProcess from 'node:child_process';
import * as fs from 'node:fs';
import fsDefault from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { runInNewContext } from 'node:vm';
import { runVersionStates } from '../test_tools/version-state-fixture.mjs';
import { parseVersion, resolveCleanBuildIdentity, checkReleaseSource } from '../integrity/source-build-identity.mjs';
import { evaluateTapSummary, runTests } from '../test_tools/run-release-tool-tests.mjs';

// 只隔离本文件创建的合成仓库；首次 init 前即生效，不修改用户 Git 配置。
const fixtureGitConfig = { 'maintenance.auto': 'false', 'gc.auto': '0', 'maintenance.autoDetach': 'false', 'gc.autoDetach': 'false' };
const fixtureGitArgs = Object.entries(fixtureGitConfig).flatMap(([key, value]) => ['-c', `${key}=${value}`]);
const git = (cwd, ...args) => execFileSync('git', [...fixtureGitArgs, ...args], { cwd, stdio: 'ignore', timeout: 10_000, env: { ...process.env, GIT_AUTHOR_DATE: '2026-10-05T00:00:00Z', GIT_COMMITTER_DATE: '2026-10-05T18:00:00Z' } });
function createFixture({ head = true, ignore = false } = {}) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-release-'));
  git(root, 'init', '-q');
  // 完整复制的本地配置也约束 version-state-fixture Worker 后续的真实 Git 写入。
  for (const [key, value] of Object.entries(fixtureGitConfig)) git(root, 'config', '--local', key, value);
  git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
  mkdirSync(path.join(root, 'ops/release/integrity'), { recursive: true });
  writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n');
  writeFileSync(path.join(root, 'source'), 'fixture');
  if (ignore) writeFileSync(path.join(root, '.gitignore'), 'ignored/\n');
  if (head) { git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture'); }
  return root;
}
// 每次测试进程只生成三类不变合成 Git 基线；每例复制完整独立目录，
// 不共享 index、refs、工作树或身份解析结果，也不复用任何验证 PASS。
const fixtureSeeds = new Map();
test.after(() => { for (const root of fixtureSeeds.values()) rmSync(root, { recursive: true, force: true }); });
function fixture({ head = true, ignore = false } = {}) {
  const key = JSON.stringify({ head, ignore });
  if (!fixtureSeeds.has(key)) fixtureSeeds.set(key, createFixture({ head, ignore }));
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-release-'));
  try {
    // 完整复制静止 seed 的全部 .git/objects；任何成员消失仍直接失败，
    // 不跳过文件、不重试半拷贝，也不复用 index/refs 或旧验证结果。
    cpSync(fixtureSeeds.get(key), root, { recursive: true, filter: () => true });
    return root;
  }
  catch (error) { rmSync(root, { recursive: true, force: true }); throw error; }
}
const withFixture = (options, body) => { const root = fixture(options); try { body(root); } finally { rmSync(root, { recursive: true, force: true }); } };
const summary = (n = 1) => `TAP version 13\n${Array.from({ length: n }, (_, i) => `ok ${i + 1} - valid`).join('\n')}\n1..${n}\n# tests ${n}\n# pass ${n}\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n`;

test('synthetic Git isolates maintenance before init even with hostile inherited config', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-maintenance-'));
  const trace = path.join(directory, 'trace.jsonl');
  const environment = { GIT_CONFIG_COUNT: '4', GIT_TRACE2_EVENT: trace };
  for (const [index, [key, value]] of Object.entries(fixtureGitConfig).entries()) {
    environment[`GIT_CONFIG_KEY_${index}`] = key;
    environment[`GIT_CONFIG_VALUE_${index}`] = value === 'false' ? 'true' : '1';
  }
  const previous = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
  const original = childProcess.execFileSync;
  let root;
  let observedInit = false;
  childProcess.execFileSync = function (command, args, options) {
    if (command === 'git') {
      assert.deepEqual(args.slice(0, fixtureGitArgs.length), fixtureGitArgs);
      if (!observedInit) {
        root = options.cwd;
        assert.equal(args[fixtureGitArgs.length], 'init');
        assert.equal(fs.existsSync(path.join(root, '.git')), false);
        // 在真实 init 发生之前读取真实命令作用域；相反的继承配置不能覆盖隔离。
        for (const [key, value] of Object.entries(fixtureGitConfig)) {
          assert.equal(original.call(this, 'git', [...fixtureGitArgs, 'config', '--get', key], {
            ...options, stdio: 'pipe', encoding: 'utf8',
          }).trim(), value);
        }
        observedInit = true;
      }
    }
    return original.call(this, command, args, options);
  };
  try {
    Object.assign(process.env, environment);
    syncBuiltinESMExports();
    assert.equal(createFixture(), root);
    assert.equal(observedInit, true);
    // seed 的本地配置必须随完整 .git 复制，供 Worker 中未加命令前缀的写操作使用。
    for (const [key, value] of Object.entries(fixtureGitConfig)) {
      assert.equal(original('git', ['-C', root, 'config', '--local', '--get', key], {
        encoding: 'utf8', timeout: 10_000,
      }).trim(), value);
    }
    const events = fs.readFileSync(trace, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert.ok(events.some(event => event.event === 'start' && event.argv.includes('commit')));
    assert.equal(events.some(event => event.event === 'child_start'
      && event.argv.some(arg => arg === 'maintenance' || arg === 'gc')), false);
  } finally {
    childProcess.execFileSync = original;
    syncBuiltinESMExports();
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    if (root) rmSync(root, { recursive: true, force: true });
    rmSync(directory, { recursive: true, force: true });
  }
});

test('fixture copy fails closed and removes its target when a source member disappears', () => {
  const original = fsDefault.cpSync;
  let lock;
  let target;
  let disappeared = false;
  fsDefault.cpSync = function (source, destination, options) {
    target = destination;
    lock = path.join(source, '.git/objects/maintenance.lock');
    writeFileSync(lock, 'synthetic disappearance');
    return original(source, destination, { ...options, filter(from, to) {
      const include = options.filter(from, to);
      if (from === lock) { assert.equal(include, true); unlinkSync(lock); disappeared = true; }
      return include;
    } });
  };
  syncBuiltinESMExports();
  try {
    assert.throws(() => fixture(), error => error.code === 'ENOENT' && error.path === lock);
    assert.equal(disappeared, true);
    assert.equal(fs.existsSync(target), false);
  } finally {
    fsDefault.cpSync = original;
    syncBuiltinESMExports();
    if (lock) rmSync(lock, { force: true });
    if (target) rmSync(target, { recursive: true, force: true });
  }
});

test('copied version fixtures isolate worktree, index, refs and cleanup', () => {
  const first = fixture(), second = fixture();
  const seed = fixtureSeeds.get(JSON.stringify({ head: true, ignore: false }));
  const members = (root, prefix = '') => fs.readdirSync(path.join(root, prefix), { withFileTypes: true })
    .sort((a, b) => a.name.localeCompare(b.name)).flatMap(entry => {
      const name = path.join(prefix, entry.name);
      return entry.isDirectory() ? [[name, 'directory'], ...members(root, name)]
        : [[name, fs.readFileSync(path.join(root, name)).toString('hex')]];
    });
  const assertMembers = (actual, expected, label) => {
    assert.deepEqual(actual.map(([name]) => name), expected.map(([name]) => name), `${label}: members`);
    for (let i = 0; i < expected.length; i++) {
      assert.equal(actual[i][1] === expected[i][1], true, `${label}: ${expected[i][0]} bytes`);
    }
  };
  // Git 原生输出核对 path/OID/mode/stage；status 允许刷新 index 的本机 stat cache。
  const indexFacts = root => execFileSync('git', ['-C', root, 'ls-files', '--stage', '-z'], {
    encoding: 'utf8', timeout: 10_000,
  });
  try {
    const expectedMembers = members(seed);
    // 此处尚无任何 Git 读取：完整树（包括原始 index）必须逐字节一致。
    assertMembers(members(first), expectedMembers, 'first copy');
    assertMembers(members(second), expectedMembers, 'second copy');
    assert.ok(expectedMembers.some(([name]) => /^\.git\/objects\/[a-f0-9]{2}\//.test(name)));
    assert.ok(expectedMembers.some(([name]) => name === '.git/index'));
    assert.ok(expectedMembers.some(([name]) => name.startsWith('.git/refs/heads/')));
    for (const [name] of expectedMembers) {
      const identities = [seed, first, second].map(root => {
        const stat = fs.statSync(path.join(root, name)); return `${stat.dev}:${stat.ino}`;
      });
      assert.equal(new Set(identities).size, 3, `${name}: no shared files or directories`);
    }
    const expectedIndex = indexFacts(seed);
    assert.equal(indexFacts(first), expectedIndex);
    assert.equal(indexFacts(second), expectedIndex);
    const expected = resolveCleanBuildIdentity(second);
    assert.deepEqual(resolveCleanBuildIdentity(first), expected);
    assert.deepEqual(resolveCleanBuildIdentity(seed), expected);
    const logicalMembers = expectedMembers.filter(([name]) => name !== '.git/index');
    const assertUnchanged = label => {
      for (const [name, root] of [['second', second], ['seed', seed]]) {
        assert.equal(indexFacts(root), expectedIndex, `${label}: ${name} staged facts`);
        assert.deepEqual(resolveCleanBuildIdentity(root), expected, `${label}: ${name} clean identity and HEAD`);
        assertMembers(members(root).filter(([file]) => file !== '.git/index'), logicalMembers, `${label}: ${name}`);
      }
    };
    writeFileSync(path.join(first, 'source'), 'independent edit');
    assert.throws(() => resolveCleanBuildIdentity(first), /release input must be clean/);
    assertUnchanged('first worktree edit');
    git(first, 'add', 'source'); git(first, 'commit', '-qm', 'independent commit');
    assert.notEqual(resolveCleanBuildIdentity(first).sourceCommit, expected.sourceCommit);
    assertUnchanged('first commit');
    rmSync(first, { recursive: true, force: true });
    assertUnchanged('first cleanup');
  } finally { rmSync(first, { recursive: true, force: true }); rmSync(second, { recursive: true, force: true }); }
});

test('version contract accepts boundaries and one final newline only', () => {
  for (const value of ['0.1.0', '65535.2.3\n', '1.2.3\r\n']) assert.ok(parseVersion(value));
  for (const value of ['', '0.0.0', '01.2.3', '1.02.3', '1.2.65536', '99999.2.3', '1.2.3\n\n', '1.2.3\r', ' 1.2.3', '1.2.3 ', '1.2.3x', '\uFEFF1.2.3', '１.2.3']) assert.throws(() => parseVersion(value));
});
test('release projection reads the version from the same full source scan and rejects committed missing/invalid input', () => {
  withFixture({}, root => {
    const { softwareVersion, sourceCommit } = resolveCleanBuildIdentity(root);
    assert.deepEqual(checkReleaseSource(root), { softwareVersion, sourceCommit });
    for (const value of ['', '0.0.0']) {
      writeFileSync(path.join(root, 'ops/release/version.txt'), value);
      git(root, 'add', '.'); git(root, 'commit', '-qm', 'invalid version');
      assert.throws(() => checkReleaseSource(root), /build source identity unavailable/);
    }
    unlinkSync(path.join(root, 'ops/release/version.txt'));
    git(root, 'add', '-u'); git(root, 'commit', '-qm', 'missing version');
    assert.throws(() => checkReleaseSource(root), /build source identity unavailable/);
  });
});
test('release source accepts clean root and ignored local output', () => withFixture({ ignore: true }, (root) => {
  mkdirSync(path.join(root, 'ignored')); writeFileSync(path.join(root, 'ignored/private'), 'x');
  const source = checkReleaseSource(root);
  assert.equal(source.softwareVersion, '1.2.3');
  assert.match(source.sourceCommit, /^[0-9a-f]{40,64}$/);
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
  const script = `import {checkReleaseSource} from ${JSON.stringify(new URL('../integrity/source-build-identity.mjs', import.meta.url).href)}; try { checkReleaseSource(process.argv[1]); process.exit(2); } catch (error) { process.stdout.write(error.message); }`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script, root], { encoding: 'utf8', env: { ...process.env, PATH: '' } });
  assert.equal(result.status, 0);
  assert.equal(result.stdout, 'build source identity unavailable');
  assert.equal(result.stderr, '');
}));
test('version CLI rejects invalid arguments and dirty release without leaking paths', () => withFixture({}, (root) => {
  const cli = path.resolve('ops/release/integrity/source-build-identity.mjs');
  const invalid = spawnSync(process.execPath, [cli, '--unknown'], { encoding: 'utf8' });
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.equal(invalid.stderr, 'release input rejected\n');
  writeFileSync(path.join(root, 'new-file'), 'secret');
  assert.throws(() => checkReleaseSource(root), (error) => !error.message.includes(root) && !error.message.includes('new-file'));
}));
test('release CLI qualifies its own clean source and rejects dirty source', () => withFixture({}, (root) => {
  const cli = path.join(root, 'ops/release/integrity/source-build-identity.mjs');
  copyFileSync(new URL('../integrity/source-build-identity.mjs', import.meta.url), cli);
  git(root, 'add', 'ops/release/integrity/source-build-identity.mjs');
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

test('snapshot input and Chrome channel mapping are deterministic and ordered', async () => {
  const { chromeVersion } = await import('../integrity/source-build-identity.mjs');
  assert.equal(parseVersion('2.0.0-SNAPSHOT\n'), '2.0.0-SNAPSHOT');
  assert.equal(chromeVersion('2.0.0-SNAPSHOT.20261006.gabc1234.dirty.123456789abc'), '2.0.0.0');
  assert.equal(chromeVersion('2.0.0'), '2.0.0.1');
  assert.equal(chromeVersion('2.0.1-SNAPSHOT.20261006.gabc1234'), '2.0.1.0');
  for (const value of ['0.0.0-SNAPSHOT', '2.0.0-preview', '2.0.0-SNAPSHOT.20261006.gABC1234', '2.0.0-SNAPSHOT.20261006.gabc1234.dirty.', '2.0.65536-SNAPSHOT', '2.0.0\n']) assert.throws(() => chromeVersion(value));
  assert.throws(() => parseVersion('2.0.0-SNAPSHOT.20261006.gabc1234'));
});
test('build identity binds commit and content, not time, staging or ignored local data', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({ ignore: true }, root => {
    const clean = resolveBuildIdentity(root);
    assert.equal(clean.softwareVersion, '1.2.3');
    assert.equal(clean.chromeVersion, '1.2.3.1');
    assert.equal(clean.dirty, false);
    assert.deepEqual(resolveBuildIdentity(root), clean);
    mkdirSync(path.join(root, 'ignored'));
    writeFileSync(path.join(root, 'ignored/private'), 'synthetic private input excluded');
    assert.deepEqual(resolveBuildIdentity(root), clean);
    writeFileSync(path.join(root, 'source'), 'changed one');
    const first = resolveBuildIdentity(root);
    assert.equal(first.channel, 'snapshot');
    assert.match(first.softwareVersion, /^1\.2\.3-SNAPSHOT\.[1-9][0-9]{7}\.g[a-f0-9]{7}\.dirty\.[a-f0-9]{12}$/);
    assert.equal(first.sourceCommit, clean.sourceCommit);
    git(root, 'add', 'source');
    assert.deepEqual(resolveBuildIdentity(root), first);
    writeFileSync(path.join(root, 'source'), 'changed two');
    const second = resolveBuildIdentity(root);
    assert.notEqual(second.buildId, first.buildId);
    assert.notEqual(second.softwareVersion, first.softwareVersion);
    writeFileSync(path.join(root, 'untracked'), 'new input');
    assert.notEqual(resolveBuildIdentity(root).buildId, second.buildId);
    unlinkSync(path.join(root, 'source'));
    let beforeLicense;
    assert.doesNotThrow(() => { beforeLicense = resolveBuildIdentity(root); });
    writeFileSync(path.join(root, 'LICENSE'), 'synthetic authorized notice');
    const withLicense = resolveBuildIdentity(root);
    assert.notEqual(withLicense.sourceSha256, beforeLicense.sourceSha256);
    assert.notEqual(withLicense.buildId, beforeLicense.buildId);
    writeFileSync(path.join(root, 'LICENSE'), 'synthetic changed notice');
    assert.notEqual(resolveBuildIdentity(root).sourceSha256, withLicense.sourceSha256);
  });
});
test('clean snapshots retain full commit identity and cannot become formal tags', async () => {
  const { resolveBuildIdentity, checkReleaseTag } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '2.0.0-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'snapshot');
    const identity = resolveBuildIdentity(root);
    assert.equal(identity.dirty, false);
    assert.equal(identity.softwareVersion, `2.0.0-SNAPSHOT.20261006.g${identity.sourceCommit.slice(0, 7)}`);
    assert.equal(checkReleaseSource(root).softwareVersion, identity.softwareVersion);
    git(root, 'tag', 'v2.0.0');
    assert.throws(() => checkReleaseTag('v2.0.0', root));
  });
});
test('formal tag requires clean numeric version and tag at exact HEAD', async () => {
  const { checkReleaseTag } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    assert.throws(() => checkReleaseTag('v1.2.3', root));
    git(root, 'tag', 'v1.2.3');
    assert.equal(checkReleaseTag('v1.2.3', root).channel, 'release');
    assert.throws(() => checkReleaseTag('v1.2.4', root));
    writeFileSync(path.join(root, 'source'), 'dirty');
    assert.throws(() => checkReleaseTag('v1.2.3', root));
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'next commit');
    assert.throws(() => checkReleaseTag('v1.2.3', root));
  });
});
test('build identity refuses symlinks rather than hashing external private input', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  const { symlinkSync } = await import('node:fs');
  withFixture({}, root => {
    symlinkSync('/nonexistent-private-source', path.join(root, 'link'));
    assert.throws(() => resolveBuildIdentity(root), /symlink forbidden/);
  });
});

const identityRaces = [
    ['file edit and restore during read', root => ({ trigger: 'source', change: () => {
      const file = path.join(root, 'source'); const original = fs.readFileSync(file);
      fs.writeFileSync(file, 'transient'); fs.writeFileSync(file, original);
    } })],
    ['previously read file edit and restore', root => ({ trigger: 'source', change: () => {
      const file = path.join(root, 'ops/release/version.txt'); const original = fs.readFileSync(file);
      fs.writeFileSync(file, 'transient'); fs.writeFileSync(file, original);
    } })],
    ['previously read file edit', root => ({ trigger: 'source', change: () => { fs.writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.4\n'); } })],
    ['tracked member addition', root => ({ trigger: 'source', change: () => { fs.writeFileSync(path.join(root, 'added'), 'x'); git(root, 'add', 'added'); } })],
    ['tracked member deletion', root => ({ trigger: 'source', change: () => { git(root, 'rm', '-q', 'source'); } })],
    ['HEAD change', root => ({ trigger: 'source', change: () => { git(root, 'commit', '--allow-empty', '-qm', 'race'); } })],
    ['version change', root => ({ trigger: 'source', change: () => { fs.writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.4\n'); } })],
  ];
for (const [name, mutate] of identityRaces) test(`identity rejects ${name} observed during bounded snapshots`, async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    const originalOpen = fsDefault.openSync;
    let changed = false;
    const race = mutate(root);
    fsDefault.openSync = function patchedOpen(filename, ...args) {
      const fd = originalOpen.call(this, filename, ...args);
      if (!changed && String(filename) === path.join(root, race.trigger)) { changed = true; race.change(); }
      return fd;
    };
    syncBuiltinESMExports();
    try { assert.throws(() => resolveBuildIdentity(root), /build source changed during identity scan/); assert.equal(changed, true); }
    finally { fsDefault.openSync = originalOpen; syncBuiltinESMExports(); }
  });
});

test('identity rejects file bytes changed and restored during an FD read', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    const originalOpen = fsDefault.openSync;
    const originalRead = fsDefault.readSync;
    const filename = path.join(root, 'source');
    const bytes = fs.readFileSync(filename);
    let targetFd, changed = false;
    fsDefault.openSync = function (...args) {
      const fd = originalOpen.apply(this, args);
      if (String(args[0]) === filename) targetFd = fd;
      return fd;
    };
    fsDefault.readSync = function (...args) {
      const count = originalRead.apply(this, args);
      if (!changed && args[0] === targetFd && count > 0) {
        changed = true;
        fs.writeFileSync(filename, 'transient'); fs.writeFileSync(filename, bytes);
      }
      return count;
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => resolveBuildIdentity(root), /build source changed during identity scan/);
      assert.equal(changed, true);
    } finally {
      fsDefault.openSync = originalOpen; fsDefault.readSync = originalRead; syncBuiltinESMExports();
    }
  });
});


test('snapshot date uses committer date in UTC+08 and ignores build clock and host timezone', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  const root = fixture();
  try {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '2.0.0-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'dated snapshot');
    const expected = resolveBuildIdentity(root);
    assert.match(expected.softwareVersion, /^2\.0\.0-SNAPSHOT\.20261006\.g[a-f0-9]{7}$/);
    assert.equal(checkReleaseSource(root).softwareVersion, expected.softwareVersion);
    const script = `import {resolveBuildIdentity} from ${JSON.stringify(new URL('../integrity/source-build-identity.mjs', import.meta.url).href)}; Date.now = () => 1; console.log(JSON.stringify(resolveBuildIdentity(process.argv[1])));`;
    // 三个独立 Node 进程仍各自执行真实完整解析；全部退出后才清理 fixture。
    const results = await Promise.all(['UTC', 'Pacific/Honolulu', 'Pacific/Auckland'].map(TZ =>
      new Promise(resolve => childProcess.execFile(process.execPath, ['--input-type=module', '-e', script, root], {
        encoding: 'utf8', timeout: 10000, env: { ...process.env, TZ },
      }, (error, stdout, stderr) => resolve({ status: error ? error.code : 0, stdout, stderr })))));
    for (const result of results) {
      assert.equal(result.status, 0, result.stderr);
      assert.deepEqual(JSON.parse(result.stdout), expected);
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('snapshot date must be a real calendar date and is bound into the development build ID', async () => {
  const { parseSoftwareVersion, resolveBuildIdentity, validateBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  assert.equal(parseSoftwareVersion('2.0.0-SNAPSHOT.20240229.gabc1234'), '2.0.0-SNAPSHOT.20240229.gabc1234');
  for (const date of ['20260229', '20260431', '20261301', '20260001', '00000101', '2026106']) {
    assert.throws(() => parseSoftwareVersion(`2.0.0-SNAPSHOT.${date}.gabc1234`));
  }
  assert.throws(() => parseSoftwareVersion('2.0.0-SNAPSHOT.gabc1234'));
  withFixture({}, root => {
    writeFileSync(path.join(root, 'source'), 'dirty fixture');
    const expected = resolveBuildIdentity(root);
    assert.equal(validateBuildIdentity(expected), expected);
    assert.throws(() => validateBuildIdentity({ ...expected,
      softwareVersion: expected.softwareVersion.replace('.20261006.', '.20261007.') }), /INVALID_BUILD_IDENTITY/);
  });
});

test('combined snapshot metadata matches the two-command Git oracle across repository states', async () => {
  const cases = ['clean', 'staged', 'unstaged', 'untracked', 'head'].map(state => ({ state, root: fixture() }));
  try { await runVersionStates(cases, 'oracle'); }
  finally { for (const { root } of cases) rmSync(root, { recursive: true, force: true }); }
});

test('clean release identity keeps four Git snapshots, two stable scans, and rejects dirty states', async () => {
  const cases = ['clean', 'staged', 'unstaged', 'untracked', 'deleted', 'snapshot'].map(state => ({
    state, root: fixture({ ignore: state === 'clean' }),
  }));
  try { await runVersionStates(cases, 'clean'); }
  finally { for (const { root } of cases) rmSync(root, { recursive: true, force: true }); }
});

test('clean identity maps porcelain overflow to release rejection inside its four snapshots', async () => {
  const { resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    const original = childProcess.execFileSync;
    let statusCalls = 0;
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git' && args[2] === 'status') {
        statusCalls++;
        assert.equal(options.maxBuffer, 100_000);
        const error = new Error('synthetic ENOBUFS'); error.code = 'ENOBUFS'; throw error;
      }
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/);
      assert.equal(statusCalls, 1);
    } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
  });
});

test('clean resolver rejects missing/invalid source, wrong roots, absent HEAD, and tracked symlinks', async () => {
  const { resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    unlinkSync(path.join(root, 'ops/release/version.txt'));
    git(root, 'add', '-u'); git(root, 'commit', '-qm', 'missing version');
    assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/);
  });
  withFixture({}, root => {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '0.0.0\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'invalid version');
    assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/);
  });
  withFixture({ head: false }, root => assert.throws(() => resolveCleanBuildIdentity(root), /build source identity unavailable/));
  withFixture({}, root => assert.throws(() => resolveCleanBuildIdentity(path.join(root, 'ops')), /build source identity unavailable/));
  const noRepo = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-clean-no-git-'));
  try { assert.throws(() => resolveCleanBuildIdentity(noRepo), /build source identity unavailable/); } finally { rmSync(noRepo, { recursive: true, force: true }); }
  withFixture({}, root => {
    unlinkSync(path.join(root, 'source'));
    fs.symlinkSync('ops/release/version.txt', path.join(root, 'source'));
    git(root, 'add', '-A'); git(root, 'commit', '-qm', 'tracked symlink');
    assert.throws(() => resolveCleanBuildIdentity(root), /build source symlink forbidden/);
  });
});

test('clean resolver rejects a tracked scan drift detected while opening an input', async () => {
  const { resolveCleanBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  withFixture({}, root => {
    const originalOpen = fsDefault.openSync;
    let changed = false;
    fsDefault.openSync = function patchedOpen(filename, ...args) {
      const fd = originalOpen.call(this, filename, ...args);
      if (!changed && String(filename) === path.join(root, 'source')) {
        changed = true;
        writeFileSync(path.join(root, 'source'), 'raced');
      }
      return fd;
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => resolveCleanBuildIdentity(root), /build source changed during identity scan/);
      assert.equal(changed, true);
    } finally { fsDefault.openSync = originalOpen; syncBuiltinESMExports(); }
  });
});

test('combined snapshot metadata supports roots with spaces, Unicode, and internal newlines', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  const parent = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-'));
  const root = path.join(parent, '空 格\nrepo');
  try {
    mkdirSync(root); git(root, 'init', '-q'); git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
    mkdirSync(path.join(root, 'ops/release/integrity'), { recursive: true });
    writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n'); writeFileSync(path.join(root, 'source'), 'fixture');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture');
    const actual = resolveBuildIdentity(root);
    assert.equal(actual.sourceCommit, execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim());
  } finally { rmSync(parent, { recursive: true, force: true }); }
});

test('combined rev-parse rejects malformed or failed output and preserves no-HEAD rejection', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  for (const malformed of [() => 'malformed', root => `${root}\n`, root => `${root}\nnot-a-commit`]) {
    withFixture({}, root => {
      const original = childProcess.execFileSync;
      childProcess.execFileSync = function (command, args, options) {
        if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) return malformed(root);
        return original.call(this, command, args, options);
      };
      syncBuiltinESMExports();
      try { assert.throws(() => resolveBuildIdentity(root), /build source identity unavailable/); }
      finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
    });
  }
  withFixture({}, root => {
    const original = childProcess.execFileSync;
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) throw new Error('synthetic git failure');
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try { assert.throws(() => resolveBuildIdentity(root), /build source identity unavailable/); }
    finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
  });
  withFixture({ head: false }, root => assert.throws(() => resolveBuildIdentity(root), /build source identity unavailable/));
  withFixture({}, root => assert.throws(() => resolveBuildIdentity(path.join(root, 'ops')), /build source identity unavailable/));
});

test('SHA-256 Git object identity uses an actual 64-digit commit', async () => {
  const { resolveBuildIdentity } = await import('../integrity/source-build-identity.mjs');
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-sha256-'));
  try {
    git(root, 'init', '-q', '--object-format=sha256');
    git(root, 'config', 'user.email', 'fixture@example.invalid'); git(root, 'config', 'user.name', 'Fixture');
    mkdirSync(path.join(root, 'ops/release/integrity'), { recursive: true });
    writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3\n'); writeFileSync(path.join(root, 'source'), 'fixture');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'fixture');
    assert.match(resolveBuildIdentity(root).sourceCommit, /^[a-f0-9]{64}$/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

function publishRunnerHandshake(readyFile, record) {
  const pendingFile = `${readyFile}.pending`;
  fs.writeFileSync(pendingFile, JSON.stringify(record), { flag: 'wx' });
  fs.renameSync(pendingFile, readyFile);
}

test('runner handshake publication hides empty and partial JSON from readers', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-publication-'));
  const readyFile = path.join(root, 'ready');
  const record = { supervisor: 123, worker: 456 };
  const visibleDuringWrite = [];
  try {
    // 在真实私有文件上固定 create、部分写入、完整写入三个时刻，不依赖调度碰运气。
    const publish = runInNewContext(`(${publishRunnerHandshake.toString()})`, {
      fs: {
        writeFileSync(file, contents, options) {
          fs.writeFileSync(file, '', options);
          visibleDuringWrite.push(fs.existsSync(readyFile));
          fs.writeFileSync(file, contents.slice(0, Math.floor(contents.length / 2)));
          visibleDuringWrite.push(fs.existsSync(readyFile));
          fs.writeFileSync(file, contents);
        },
        renameSync: fs.renameSync,
      },
    });
    publish(readyFile, record);
    assert.deepEqual(visibleDuringWrite, [false, false], 'reader must not see the final name before complete JSON is published');
    assert.deepEqual(JSON.parse(fs.readFileSync(readyFile, 'utf8')), record);
    assert.deepEqual(fs.readdirSync(root), ['ready'], 'publication leaves no staging file');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

function runnerChildScript(readyFile, heartbeatFile, workerReadyFile, overflow = false) {
  const publish = publishRunnerHandshake.toString();
  const workerCode = `const fs=require('node:fs');${publish};process.on('SIGTERM',()=>{});fs.writeFileSync(${JSON.stringify(heartbeatFile)},'x');publishRunnerHandshake(${JSON.stringify(workerReadyFile)},{worker:process.pid});setInterval(()=>fs.appendFileSync(${JSON.stringify(heartbeatFile)},'x'),20);`;
  return `const fs=require('node:fs');const cp=require('node:child_process');${publish};const worker=cp.spawn(process.execPath,['-e',${JSON.stringify(workerCode)}],{stdio:'ignore'});const until=Date.now()+2500;const poll=()=>{if(fs.existsSync(${JSON.stringify(workerReadyFile)})){publishRunnerHandshake(${JSON.stringify(readyFile)},{supervisor:process.pid,worker:JSON.parse(fs.readFileSync(${JSON.stringify(workerReadyFile)},'utf8')).worker});${overflow ? "process.stdout.write('x'.repeat(1000));" : ''}setInterval(()=>{},1000);return;}if(Date.now()>until)process.exit(3);setTimeout(poll,5)};poll();`;
}

async function waitForRunnerHandshake(readyFile) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (fs.existsSync(readyFile)) return JSON.parse(fs.readFileSync(readyFile, 'utf8'));
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  throw new Error('runner child handshake timed out');
}

function pidIsDead(pid) {
  try { process.kill(pid, 0); return false; }
  catch (error) { if (error.code === 'ESRCH') return true; throw error; }
}

async function assertRunnerPidsReaped(pids) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (pids.every(pidIsDead)) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.deepEqual(pids.filter(pid => !pidIsDead(pid)), [], 'runner-owned supervisor and worker must be ESRCH before test cleanup');
}

function spawnTracked(command, args, options = {}) {
  const child = childProcess.spawn(command, args, { ...options, stdio: options.stdio ?? 'ignore' });
  child.exitResult = new Promise(resolve => child.once('close', (code, signal) => resolve({ code, signal })));
  return child;
}

async function waitForExit(child, timeoutMs = 3000) {
  let deadline;
  try {
    return await Promise.race([
      child.exitResult,
      new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error(`owned process ${child.pid} did not exit within ${timeoutMs}ms`)), timeoutMs); }),
    ]);
  } finally { clearTimeout(deadline); }
}

async function assertSentinelAlive(pid) {
  assert.doesNotThrow(() => process.kill(pid, 0), 'independent sentinel remains alive');
}

test('runner timeout and overflow kill their owned groups without touching a detached sentinel', async () => {
  const { runTests } = await import('../test_tools/run-release-tool-tests.mjs');
  for (const mode of ['timeout', 'overflow']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-'));
    const readyFile = path.join(root, 'ready'); const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const sentinelReady = path.join(root, 'sentinel-ready');
    let supervisor;
    let sentinel;
    let bodyError;
    try {
      sentinel = spawnTracked(process.execPath, ['-e', `require('node:fs').writeFileSync(${JSON.stringify(sentinelReady)},'ready');setInterval(()=>{},1000)`], { detached: true });
      sentinel.unref();
      const sentinelPid = sentinel.pid;
      const sentinelDeadline = Date.now() + 3000;
      while (!fs.existsSync(sentinelReady) && Date.now() < sentinelDeadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(fs.existsSync(sentinelReady), 'independent sentinel handshake');
      const args = ['-e', runnerChildScript(readyFile, heartbeatFile, workerReadyFile, mode === 'overflow')];
      const resultPromise = runTests({ command: process.execPath, args, timeoutMs: mode === 'timeout' ? 3000 : 10000, maxOutputBytes: mode === 'overflow' ? 64 : 10000 });
      const ready = await waitForRunnerHandshake(readyFile);
      supervisor = ready.supervisor;
      await assertSentinelAlive(sentinelPid);
      const result = await resultPromise;
      assert.equal(result.status, 'FAIL');
      assert.equal(result.reason, mode === 'timeout' ? 'test process timed out' : 'test process output exceeded limit');
      await assertRunnerPidsReaped([ready.supervisor, ready.worker]);
      await assertSentinelAlive(sentinelPid);
    } catch (error) {
      bodyError = error;
      throw error;
    } finally {
      if (supervisor) { try { process.kill(-supervisor, 'SIGKILL'); } catch {} }
      if (sentinel?.pid) { try { process.kill(-sentinel.pid, 'SIGKILL'); } catch {} }
      try { rmSync(root, { recursive: true, force: true }); }
      catch (cleanupError) {
        if (bodyError) throw new AggregateError([bodyError, cleanupError], 'runner fixture failed and cleanup also failed');
        throw cleanupError;
      }
    }
  }
});

test('runner forwards parent TERM and INT after a real worker handshake and restores listeners', async () => {
  const { runTests } = await import('../test_tools/run-release-tool-tests.mjs');
  for (const signal of ['SIGTERM', 'SIGINT']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-cancel-'));
    const readyFile = path.join(root, 'ready'); const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const termBefore = process.listenerCount('SIGTERM'); const intBefore = process.listenerCount('SIGINT');
    let ready;
    try {
      const resultPromise = runTests({ command: process.execPath, args: ['-e', runnerChildScript(readyFile, heartbeatFile, workerReadyFile)], timeoutMs: 10000 });
      ready = await waitForRunnerHandshake(readyFile);
      process.emit(signal);
      const result = await resultPromise;
      assert.equal(result.status, 'FAIL'); assert.equal(result.reason, 'test process cancelled');
      await assertRunnerPidsReaped([ready.supervisor, ready.worker]);
      assert.equal(process.listenerCount('SIGTERM'), termBefore);
      assert.equal(process.listenerCount('SIGINT'), intBefore);
    } finally {
      if (ready) {
        try { process.kill(-ready.supervisor, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
        for (const pid of [ready.supervisor, ready.worker]) { try { process.kill(pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } }
      }
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test('runner private child group does not escape real wrapper-group TERM and INT cancellation', async () => {
  const wrapperModule = new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href;
  for (const signal of ['SIGTERM', 'SIGINT']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-wrapper-'));
    const readyFile = path.join(root, 'ready'); const childReady = path.join(root, 'child-ready');
    const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const sentinelReady = path.join(root, 'sentinel-ready');
    const wrapperCode = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(wrapperModule)};${publishRunnerHandshake.toString()};const args=['-e',${JSON.stringify(runnerChildScript(childReady, heartbeatFile, workerReadyFile))}];const pending=runTests({command:process.execPath,args,timeoutMs:15000});const end=Date.now()+4000;while(!fs.existsSync(${JSON.stringify(childReady)})&&Date.now()<end)await new Promise(r=>setTimeout(r,10));if(!fs.existsSync(${JSON.stringify(childReady)}))process.exit(4);publishRunnerHandshake(${JSON.stringify(readyFile)},{wrapper:process.pid,...JSON.parse(fs.readFileSync(${JSON.stringify(childReady)},'utf8'))});const result=await pending;process.exitCode=result.status==='PASS'?0:1;`;
    let wrapper; let sentinel;
    let owned = []; let ownedGroupLeaders = [];
    try {
      sentinel = spawnTracked(process.execPath, ['-e', `require('node:fs').writeFileSync(${JSON.stringify(sentinelReady)},'ready');setInterval(()=>{},1000)`], { detached: true });
      const sentinelPid = sentinel.pid;
      const sentinelDeadline = Date.now() + 3000;
      while (!fs.existsSync(sentinelReady) && Date.now() < sentinelDeadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(fs.existsSync(sentinelReady), 'independent sentinel handshake');
      wrapper = spawnTracked(process.execPath, ['--input-type=module', '-e', wrapperCode], { detached: true });
      const ready = await waitForRunnerHandshake(readyFile);
      owned = [ready.wrapper, ready.supervisor, ready.worker];
      ownedGroupLeaders = [ready.wrapper, ready.supervisor];
      await assertSentinelAlive(sentinelPid);
      process.kill(-wrapper.pid, signal);
      await new Promise(resolve => setTimeout(resolve, 500));
      if (!pidIsDead(ready.wrapper)) {
        try { process.kill(-wrapper.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      }
      const exit = await waitForExit(wrapper, 3000);
      assert.ok(exit.code !== 0 || exit.signal !== null, 'wrapper must not report successful completion');
      await assertRunnerPidsReaped(owned);
      await assertSentinelAlive(sentinelPid);
    } finally {
      if (wrapper?.pid && !pidIsDead(wrapper.pid)) { try { process.kill(-wrapper.pid, 'SIGKILL'); } catch {} }
      // 组长退出不代表同组后代已退出；清理仍使用本次握手绑定的组。
      for (const pid of ownedGroupLeaders) { try { process.kill(-pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } }
      if (wrapper) await waitForExit(wrapper, 3000).catch(() => {});
      if (sentinel?.pid && !pidIsDead(sentinel.pid)) { try { process.kill(-sentinel.pid, 'SIGKILL'); } catch {} }
      if (sentinel?.pid) await waitForExit(sentinel, 3000).catch(() => {});
      rmSync(root, { recursive: true, force: true });
    }
  }
});

test('outer runner timeout and overflow clean nested private groups and preserve an external sentinel', async () => {
  const wrapperModule = new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href;
  for (const mode of ['timeout', 'overflow']) {
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-nested-'));
    const readyFile = path.join(root, 'ready'); const childReady = path.join(root, 'child-ready');
    const heartbeatFile = path.join(root, 'heartbeat'); const workerReadyFile = path.join(root, 'worker-ready');
    const sentinelReady = path.join(root, 'sentinel-ready');
    const wrapperCode = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(wrapperModule)};${publishRunnerHandshake.toString()};const pending=runTests({command:process.execPath,args:['-e',${JSON.stringify(runnerChildScript(childReady, heartbeatFile, workerReadyFile))}],timeoutMs:15000});const end=Date.now()+4000;while(!fs.existsSync(${JSON.stringify(childReady)})&&Date.now()<end)await new Promise(r=>setTimeout(r,10));if(!fs.existsSync(${JSON.stringify(childReady)}))process.exit(4);publishRunnerHandshake(${JSON.stringify(readyFile)},{wrapper:process.pid,...JSON.parse(fs.readFileSync(${JSON.stringify(childReady)},'utf8'))});${mode === 'overflow' ? "process.stdout.write('x'.repeat(1000));" : ''}await pending;`;
    let sentinel; let ready;
    try {
      sentinel = spawnTracked(process.execPath, ['-e', `require('node:fs').writeFileSync(${JSON.stringify(sentinelReady)},'ready');setInterval(()=>{},1000)`], { detached: true });
      const sentinelDeadline = Date.now() + 3000;
      while (!fs.existsSync(sentinelReady) && Date.now() < sentinelDeadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(fs.existsSync(sentinelReady), 'independent sentinel handshake');
      const pending = runTests({ command: process.execPath, args: ['--input-type=module', '-e', wrapperCode], timeoutMs: mode === 'timeout' ? 3000 : 10000, maxOutputBytes: mode === 'overflow' ? 64 : 10000 });
      ready = await waitForRunnerHandshake(readyFile);
      const result = await pending;
      assert.equal(result.status, 'FAIL');
      assert.equal(result.reason, mode === 'timeout' ? 'test process timed out' : 'test process output exceeded limit');
      await assertRunnerPidsReaped([ready.wrapper, ready.supervisor, ready.worker]);
      await assertSentinelAlive(sentinel.pid);
    } finally {
      if (ready) for (const leader of [ready.wrapper, ready.supervisor]) { try { process.kill(-leader, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } }
      if (sentinel?.pid) { try { process.kill(-sentinel.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } await waitForExit(sentinel); }
      rmSync(root, { recursive: true, force: true });
    }
  }
});

for (const signal of ['SIGTERM', 'SIGINT']) {
  test(`nested runners forward real external ${signal} and preserve an independent sentinel`, async () => {
    const module = new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href;
    const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runner-nested-external-'));
    const childReady = path.join(root, 'child-ready'); const middleReady = path.join(root, 'middle-ready');
    const readyFile = path.join(root, 'ready'); const resultFile = path.join(root, 'result');
    const sentinelReady = path.join(root, 'sentinel-ready');
    const middle = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(module)};${publishRunnerHandshake.toString()};const pending=runTests({command:process.execPath,args:['-e',${JSON.stringify(runnerChildScript(childReady, path.join(root, 'heartbeat'), path.join(root, 'worker-ready')))}],timeoutMs:15000});while(!fs.existsSync(${JSON.stringify(childReady)}))await new Promise(r=>setTimeout(r,10));publishRunnerHandshake(${JSON.stringify(middleReady)},{middle:process.pid,...JSON.parse(fs.readFileSync(${JSON.stringify(childReady)},'utf8'))});const result=await pending;process.exitCode=result.status==='PASS'?0:1;`;
    const outer = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(module)};${publishRunnerHandshake.toString()};const pending=runTests({command:process.execPath,args:['--input-type=module','-e',${JSON.stringify(middle)}],timeoutMs:15000});while(!fs.existsSync(${JSON.stringify(middleReady)}))await new Promise(r=>setTimeout(r,10));publishRunnerHandshake(${JSON.stringify(readyFile)},{outer:process.pid,...JSON.parse(fs.readFileSync(${JSON.stringify(middleReady)},'utf8'))});const result=await pending;fs.writeFileSync(${JSON.stringify(resultFile)},JSON.stringify(result));process.exitCode=result.status==='PASS'?0:1;`;
    let wrapper; let sentinel; let ready;
    try {
      sentinel = spawnTracked(process.execPath, ['-e', `require('node:fs').writeFileSync(${JSON.stringify(sentinelReady)},'ready');setInterval(()=>{},1000)`], { detached: true });
      const deadline = Date.now() + 3000;
      while (!fs.existsSync(sentinelReady) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
      assert.ok(fs.existsSync(sentinelReady), 'independent sentinel handshake');
      wrapper = spawnTracked(process.execPath, ['--input-type=module', '-e', outer], { detached: true });
      ready = await waitForRunnerHandshake(readyFile);
      assert.equal(ready.outer, wrapper.pid);
      await assertSentinelAlive(sentinel.pid);
      process.kill(-wrapper.pid, signal);
      const exit = await waitForExit(wrapper, 3000);
      assert.equal(exit.code, 1, 'external cancellation must retain failed exit semantics');
      const result = JSON.parse(fs.readFileSync(resultFile, 'utf8'));
      assert.equal(result.status, 'FAIL');
      assert.equal(result.reason, 'test process cancelled');
      await assertRunnerPidsReaped([ready.outer, ready.middle, ready.supervisor, ready.worker]);
      await assertSentinelAlive(sentinel.pid);
    } finally {
      // Only this invocation's handshaken groups may be reaped; a dead leader is not proof of cleanup.
      for (const pid of [ready?.supervisor, ready?.middle, wrapper?.pid].filter(Boolean)) {
        try { process.kill(-pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
      }
      if (wrapper) await waitForExit(wrapper, 3000).catch(() => {});
      if (sentinel?.pid) { try { process.kill(-sentinel.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } await waitForExit(sentinel); }
      rmSync(root, { recursive: true, force: true });
    }
  });
}

test('runner preserves TAP pass, nonzero exit, and spawn-error results', async () => {
  const { runTests } = await import('../test_tools/run-release-tool-tests.mjs');
  const tap = 'TAP version 13\n1..1\nok 1 - runner direct pass\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n';
  const pass = await runTests({ command: process.execPath, args: ['-e', `process.stdout.write(${JSON.stringify(tap)})`] });
  assert.deepEqual(pass, { status: 'PASS', checks_run: 1, failures: 0, errors: 0, skipped: 0, reason: '' });
  const nonzero = await runTests({ command: process.execPath, args: ['-e', 'process.exit(7)'] });
  assert.equal(nonzero.status, 'FAIL');
  const spawnError = await runTests({ command: '/not/a/real/runner', args: [] });
  assert.equal(spawnError.status, 'FAIL'); assert.equal(spawnError.errors, 1);
});

test('fixed CLI suites preserve exact files and budgets and reject free-form overrides', () => {
  const entry = path.resolve('ops/release/test_tools/run-release-tool-tests.mjs');
  const expected = {
    version: [60_000, ['version']],
    build: [170_000, ['archive-identity', 'embedded-identity', 'build', 'build-execution', 'single-image', 'candidate', 'verified-candidate', 'candidate-proof', 'pipeline', 'workflow', 'synthetic-candidates', 'runner']],
    manifest: [80_000, ['manifest']],
    'runtime-entry': [80_000, ['runtime-entry']],
    lifecycle: [600_000, ['lifecycle']],
    'lifecycle-install': [120_000, ['lifecycle-install']],
  };
  const script = `import cp from 'node:child_process';import {syncBuiltinESMExports} from 'node:module';
    const spawn=cp.spawn;const timers=[];const timer=globalThis.setTimeout;globalThis.setTimeout=(fn,ms,...args)=>{timers.push(ms);return timer(fn,ms,...args)};
    const calls=[];cp.spawn=(command,args,options)=>{calls.push({command,args});return spawn(process.execPath,['-e',${JSON.stringify(`process.stdout.write(${JSON.stringify(summary())})`)}],options)};syncBuiltinESMExports();
    process.argv=[process.execPath,${JSON.stringify(entry)},...JSON.parse(process.argv[1])];
    const output=[];console.log=value=>output.push(JSON.parse(value));
    await import(${JSON.stringify(new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href)});
    process.on('beforeExit',()=>process.stdout.write(JSON.stringify({calls,timers,output})));`;
  for (const [suite, [budget, names]] of Object.entries(expected)) {
    const argv = suite === 'version' ? [] : ['--suite', suite];
    const run = spawnSync(process.execPath, ['--input-type=module', '-e', script, JSON.stringify(argv)], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(run.status, 0, run.stderr);
    const { calls, timers, output } = JSON.parse(run.stdout);
    assert.deepEqual(calls, [{ command: process.execPath, args: [...(suite === 'build' ? ['--experimental-test-module-mocks'] : []), '--test', '--test-reporter=tap', ...names.map(name => path.join(path.dirname(entry), `../tests/${name}.test.mjs`))] }]);
    assert.deepEqual(timers, [budget]);
    assert.equal(output[0].status, 'PASS');
  }
  for (const argv of [['--suite', 'version'], ['--suite', 'missing'], ['--suite', '../version'], ['--command', 'sh'], ['--suite', 'build', '--timeout', '1'], ['--suite']]) {
    const run = spawnSync(process.execPath, ['--input-type=module', '-e', script, JSON.stringify(argv)], { encoding: 'utf8', timeout: 10_000 });
    assert.equal(run.status, 1);
    const { calls, timers, output } = JSON.parse(run.stdout);
    assert.deepEqual(calls, []); assert.deepEqual(timers, []);
    assert.equal(output[0].status, 'FAIL'); assert.equal(output[0].reason, 'invalid arguments');
  }
});

test('release projection uses one 15 second deadline for every Git process and source read', t => {
  withFixture({}, root => {
    const original = childProcess.execFileSync;
    let elapsed = 0;
    t.mock.method(performance, 'now', () => elapsed);
    const budgets = [];
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git') {
        budgets.push(options.timeout);
        assert.equal(options.timeout, Math.min(10_000, 15_000 - elapsed));
        const result = original.call(this, command, args, options);
        elapsed += 1100;
        return result;
      }
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try {
      assert.throws(() => checkReleaseSource(root), /build source identity timed out/);
      assert.ok(budgets.some(ms => ms < 10_000), 'late subprocesses consume remaining total budget');
      assert.ok(budgets.every(ms => ms > 0));
      assert.ok(budgets.length < 16, 'expired deadline does not start another full Git snapshot');
    } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); t.mock.restoreAll(); }
  });
});

test('source file reads consume the same deadline and close the active file on expiry', t => {
  withFixture({}, root => {
    const read = fsDefault.readSync; const close = fsDefault.closeSync;
    let elapsed = 0; let openedFd; let closed = false;
    t.mock.method(performance, 'now', () => elapsed);
    fsDefault.readSync = function (fd, ...args) {
      openedFd = fd;
      const count = read.call(this, fd, ...args);
      elapsed = 15_001;
      return count;
    };
    fsDefault.closeSync = function (fd) { if (fd === openedFd) closed = true; return close.call(this, fd); };
    syncBuiltinESMExports();
    try {
      assert.throws(() => checkReleaseSource(root), /build source identity timed out/);
      assert.ok(closed, 'timed-out source read still releases its own descriptor');
    } finally {
      fsDefault.readSync = read; fsDefault.closeSync = close; syncBuiltinESMExports(); t.mock.restoreAll();
    }
  });
});

// ownership 追加矩阵：原50项正文保持原字节；只使用当前测试创建的能力／子进程。
function isolatedOwnershipEnv() {
  const env = { ...process.env };
  delete env.LEXIFLOW_RUNNER_CONTEXT;
  delete env.LEXIFLOW_RUNNER_BOOTSTRAP;
  delete env.NODE_TEST_CONTEXT; // 独立 fixture root 不是上级 Node test worker。
  return env;
}
async function ownershipFixture(body) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-ownership-'));
  const children = []; const owned = new Set();
  const track = (code, options = {}) => {
    const child = spawnTracked(process.execPath, ['--input-type=module', '-e', code], { detached: true, env: isolatedOwnershipEnv(), stdio: ['ignore', 'ignore', 'pipe'], ...options });
    child.stderr?.pipe(process.stderr, { end: false });
    children.push(child); owned.add(child.pid); return child;
  };
  try { await body({ root, track, owned }); }
  finally {
    // 只对仍存活的本次直接子进程发信号；失效数值 PID 不作补杀。
    for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await Promise.all(children.map(child => waitForExit(child).catch(() => {})));
    // 失败路径也保存并观察本 fixture 已发布的所有 owned PID，不按进程名收编。
    const relays = new Set();
    const collect = value => {
      if (!value || typeof value !== 'object') return;
      for (const [key, item] of Object.entries(value)) {
        if (['supervisor', 'worker', 'middle', 'guard', 'outer', 'sentinel'].includes(key) && Number.isSafeInteger(item)) owned.add(item);
        else if (key === 'relayDirectory' && typeof item === 'string' && path.basename(item).startsWith('lf-owned-')) relays.add(item);
        else collect(item);
      }
    };
    for (const name of fs.readdirSync(root)) { try { collect(JSON.parse(fs.readFileSync(path.join(root, name), 'utf8'))); } catch {} }
    await assertRunnerPidsReaped([...owned]);
    for (const directory of relays) rmSync(directory, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
}
const ownershipModule = new URL('../test_tools/runner-ownership-bootstrap.cjs', import.meta.url).href;
const testRunnerModule = new URL('../test_tools/run-release-tool-tests.mjs', import.meta.url).href;
const ownershipWaitCode = `const wait=async file=>{const until=Date.now()+3000;while(!fs.existsSync(file)){if(Date.now()>until)throw Error('fixture handshake');await new Promise(r=>setTimeout(r,5));}return JSON.parse(fs.readFileSync(file,'utf8'));};`;

// 无载荷的协议测试在隔离 root 内执行，跨 run token 不等于继承同一 endpoint。
test('ownership rejects stale cross-run duplicate and malformed registrations without numeric signals', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const done = path.join(root, 'done');
    const code = `import assert from 'node:assert/strict';import fs from 'node:fs';import net from 'node:net';import {openOwnership} from ${JSON.stringify(ownershipModule)};
      const signals=[];const kill=process.kill;process.kill=(pid,signal)=>{signals.push({pid,signal});return kill(pid,signal)};
      const leases=[];const raw=async(endpoint,message)=>new Promise((resolve,reject)=>{const s=net.createConnection(endpoint);s.on('error',()=>{});s.on('close',resolve);s.on('data',()=>reject(Error('invalid capability accepted')));s.on('connect',()=>{const text=typeof message==='string'?message:JSON.stringify(message)+'\\n';s.write(text.slice(0,7));setImmediate(()=>s.write(text.slice(7)));});});
      try {
        const first=await openOwnership(()=>{});const second=await openOwnership(()=>{});leases.push(first,second);
        const a=JSON.parse(first.env.LEXIFLOW_RUNNER_BOOTSTRAP),b=JSON.parse(second.env.LEXIFLOW_RUNNER_BOOTSTRAP);
        assert.notEqual(a.endpoint,b.endpoint);
        await raw(b.endpoint,{op:'guard',token:a.token,leader:process.pid});
        await raw(a.endpoint,{op:'reserve',parent:'invalid',seq:1});await raw(a.endpoint,'{malformed\\n');
        await first.noSpawn();assert.equal(await first.finish(),true);
        await raw(a.endpoint,{op:'guard',token:a.token,leader:process.pid});
        await raw(a.endpoint,{op:'reserve',parent:a.token,seq:1});
        await second.noSpawn();await assert.rejects(second.noSpawn());
        assert.deepEqual(signals,[],'no nonzero or numeric-PID fallback on invalid registration');
        fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({status:'PASS',signals}));
      } finally {await Promise.all(leases.map(l=>l.dispose()));}`;
    const child = track(code);
    assert.deepEqual(await waitForExit(child), { code: 0, signal: null });
    assert.equal(JSON.parse(fs.readFileSync(done)).status, 'PASS');
  });
});

test('ownership freezes registration before spawn and cancels guard-ready payload exactly once', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const done = path.join(root, 'done'); const payload = path.join(root, 'payload');
    const code = `import assert from 'node:assert/strict';import fs from 'node:fs';import net from 'node:net';import {spawn} from 'node:child_process';import {openOwnership} from ${JSON.stringify(ownershipModule)};
      const leases=[];try {
        const {runTests}=await import(${JSON.stringify(testRunnerModule)});
        const startup=await runTests({timeoutMs:1,args:['-e',${JSON.stringify(`require('node:fs').writeFileSync(${JSON.stringify(payload)},'must not execute')`)}]});
        assert.equal(startup.reason,'test process timed out');assert.equal(fs.existsSync(${JSON.stringify(payload)}),false);
        const early=await openOwnership(()=>{});leases.push(early);
        const stopping=early.cancel();await early.noSpawn();assert.equal(await stopping,true);
        const boot=JSON.parse(early.env.LEXIFLOW_RUNNER_BOOTSTRAP);
        await new Promise((resolve,reject)=>{const s=net.createConnection(boot.endpoint);s.on('error',()=>{});s.on('close',resolve);s.on('data',()=>reject(Error('spawn after freeze')));s.on('connect',()=>s.write(JSON.stringify({op:'reserve',seq:1,parent:boot.token})+'\\n'));});
        const lease=await openOwnership(()=>{});leases.push(lease);
        const child=spawn(process.execPath,['-e',${JSON.stringify(`require('node:fs').writeFileSync(${JSON.stringify(payload)},'must not execute');setInterval(()=>{},1000)`)}],{detached:true,stdio:['ignore','ignore','ignore','pipe'],env:lease.env});
        const exited=new Promise(r=>child.once('close',(code,signal)=>r({code,signal})));
        assert.equal(await lease.attach(child.pid),true);assert.equal(await lease.ready(),true);child.stdio[3].write('{"op":"armed"}\\n');
        await new Promise((resolve,reject)=>{let text='';child.stdio[3].on('error',reject);child.stdio[3].on('data',c=>{text+=c;if(text.endsWith('\\n')){assert.equal(text,'{"op":"ready"}\\n');resolve();}});});
        const active=JSON.parse(lease.env.LEXIFLOW_RUNNER_BOOTSTRAP);
        await new Promise((resolve,reject)=>{const s=net.createConnection(active.endpoint);s.on('error',()=>{});s.on('close',resolve);s.on('data',()=>reject(Error('duplicate guard accepted')));s.on('connect',()=>s.write(JSON.stringify({op:'guard',token:active.token,leader:child.pid})+'\\n'));});
        const results=await Promise.all(Array.from({length:8},()=>lease.cancel()));assert.ok(results.every(Boolean));
        await exited;assert.equal(fs.existsSync(${JSON.stringify(payload)}),false);
        assert.throws(()=>process.kill(-child.pid,0),{code:'ESRCH'});
        assert.equal(await lease.finish(),true);fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({status:'PASS',repeated:results.length}));
      } finally {await Promise.all(leases.map(l=>l.dispose()));}`;
    const child = track(code);
    assert.deepEqual(await waitForExit(child), { code: 0, signal: null });
    assert.equal(JSON.parse(fs.readFileSync(done)).repeated, 8);
  });
});

test('ownership normal TAP exit cleans a surviving worker holding output before returning PASS', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const readyFile = path.join(root, 'ready'); const workerFile = path.join(root, 'worker');
    const sentinel = track(`setInterval(()=>{},1000)`);
    const worker = `const fs=require('node:fs');${publishRunnerHandshake.toString()};process.on('SIGTERM',()=>{});publishRunnerHandshake(${JSON.stringify(workerFile)},{worker:process.pid});setInterval(()=>{},1000);`;
    const code = `const fs=require('node:fs');${publishRunnerHandshake.toString()};const w=require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(worker)}],{stdio:['ignore','inherit','inherit']});const poll=()=>{if(!fs.existsSync(${JSON.stringify(workerFile)}))return setTimeout(poll,5);publishRunnerHandshake(${JSON.stringify(readyFile)},{supervisor:process.pid,...JSON.parse(fs.readFileSync(${JSON.stringify(workerFile)}))});process.stdout.write(${JSON.stringify(summary())},()=>process.exit(0));};poll();`;
    const result = await runTests({ args: ['-e', code] });
    const ready = await waitForRunnerHandshake(readyFile);
    assert.equal(result.status, 'PASS', JSON.stringify(result));
    await assertRunnerPidsReaped([ready.supervisor, ready.worker]);
    assert.throws(() => process.kill(-ready.supervisor, 0), { code: 'ESRCH' }, 'whole group, not only payload exit');
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership root cleans a prematurely exited middle layer leaf-to-root without PID fallback', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const leafFile = path.join(root, 'leaf'); const done = path.join(root, 'done');
    const middle = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};${publishRunnerHandshake.toString()};${ownershipWaitCode}void runTests({args:['-e',${JSON.stringify(runnerChildScript(leafFile, path.join(root, 'beat'), path.join(root, 'worker')))}]});await wait(${JSON.stringify(leafFile)});process.exit(9);`;
    const code = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};const observed=[];const signals=[];const kill=process.kill;process.kill=(pid,signal)=>{signals.push(signal);try{return kill(pid,signal);}catch(e){if(e.code==='ESRCH')observed.push(-pid);throw e;}};const result=await runTests({args:['--input-type=module','-e',${JSON.stringify(middle)}]});fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({result,observed,signals}));`;
    const sentinel = track('setInterval(()=>{},1000)'); const outer = track(code);
    assert.deepEqual(await waitForExit(outer), { code: 0, signal: null });
    const ready = await waitForRunnerHandshake(leafFile); const result = JSON.parse(fs.readFileSync(done));
    assert.equal(result.result.status, 'FAIL');
    assert.notEqual(result.result.cleanup_succeeded, false, JSON.stringify(result));
    assert.ok(result.observed.length >= 2);
    assert.equal(result.observed[0], ready.supervisor, 'leaf group CLEAN precedes parent lease release');
    assert.ok(result.signals.every(signal => signal === 0), 'root observes only; live guard owns group termination');
    await assertRunnerPidsReaped([ready.supervisor, ready.worker]);
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership depth-three cancellation preserves a live sibling until its own cancellation', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const aLeaf = path.join(root, 'a-leaf'); const bLeaf = path.join(root, 'b-leaf'); const done = path.join(root, 'done');
    const aMiddle = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};${ownershipWaitCode}await runTests({args:['-e',${JSON.stringify(runnerChildScript(aLeaf, path.join(root, 'a-beat'), path.join(root, 'a-worker')))}]});`;
    const code = `import assert from 'node:assert/strict';import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};${ownershipWaitCode}
      const a=runTests({args:['--input-type=module','-e',${JSON.stringify(aMiddle)}],timeoutMs:1500});
      const b=runTests({args:['-e',${JSON.stringify(runnerChildScript(bLeaf, path.join(root, 'b-beat'), path.join(root, 'b-worker')))}]});
      const ar=await wait(${JSON.stringify(aLeaf)}),br=await wait(${JSON.stringify(bLeaf)});const first=await a;
      assert.equal(first.reason,'test process timed out');assert.notEqual(first.cleanup_succeeded,false);
      assert.throws(()=>process.kill(ar.worker,0),{code:'ESRCH'});assert.doesNotThrow(()=>process.kill(br.worker,0));
      process.emit('SIGTERM');const second=await b;assert.equal(second.reason,'test process cancelled');assert.notEqual(second.cleanup_succeeded,false);
      fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({first,second,ar,br}));`;
    const sentinel = track('setInterval(()=>{},1000)'); const outer = track(code);
    assert.deepEqual(await waitForExit(outer), { code: 0, signal: null });
    const result = JSON.parse(fs.readFileSync(done));
    await assertRunnerPidsReaped([result.ar.supervisor, result.ar.worker, result.br.supervisor, result.br.worker]);
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership unexpected guard death fails closed and invalidates its numeric group capability', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const readyFile = path.join(root, 'ready'); const done = path.join(root, 'done');
    const payload = `const fs=require('node:fs');${publishRunnerHandshake.toString()};const cp=require('node:child_process');const rows=cp.execFileSync('/bin/ps',['-axo','pid=,ppid='],{encoding:'utf8'}).trim().split('\\n').map(r=>r.trim().split(/\\s+/).map(Number));const guard=rows.find(r=>r[1]===process.pid)[0];publishRunnerHandshake(${JSON.stringify(readyFile)},{supervisor:process.pid,guard});setInterval(()=>{},1000);`;
    const code = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};const signals=[];const kill=process.kill;process.kill=(pid,signal)=>{signals.push(signal);return kill(pid,signal)};const result=await runTests({args:['-e',${JSON.stringify(payload)}]});fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({result,signals}));`;
    const sentinel = track('setInterval(()=>{},1000)'); const outer = track(code);
    const ready = await waitForRunnerHandshake(readyFile);
    // 唯一 owned guard 是该受控 leader 的直接子进程；不是 kill-by-name。
    process.kill(ready.guard, 'SIGKILL');
    await waitForExit(outer);
    const result = JSON.parse(fs.readFileSync(done));
    assert.equal(result.result.status, 'FAIL');
    assert.equal(result.result.cleanup_succeeded, false, 'lost live guard must not claim normal CLEAN');
    assert.ok(result.signals.every(signal => signal === 0));
    await assertRunnerPidsReaped([ready.supervisor, ready.guard]);
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership hardkill owner EOF fallback is separate from the normal cancellation barrier', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const leafFile = path.join(root, 'leaf'); const readyFile = path.join(root, 'ready');
    const middle = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};${publishRunnerHandshake.toString()};${ownershipWaitCode}void runTests({args:['-e',${JSON.stringify(runnerChildScript(leafFile, path.join(root, 'beat'), path.join(root, 'worker')))}]});const ready=await wait(${JSON.stringify(leafFile)});publishRunnerHandshake(${JSON.stringify(readyFile)},{middle:process.pid,relayDirectory:JSON.parse(process.env.LEXIFLOW_RUNNER_CONTEXT).endpoint.replace(/\\/[^/]+$/,''),...ready});`;
    const code = `import {runTests} from ${JSON.stringify(testRunnerModule)};await runTests({args:['--input-type=module','-e',${JSON.stringify(middle)}]});`;
    const sentinel = track('setInterval(()=>{},1000)'); const outer = track(code);
    const ready = await waitForRunnerHandshake(readyFile);
    outer.kill('SIGKILL');
    assert.deepEqual(await waitForExit(outer), { code: null, signal: 'SIGKILL' });
    await assertRunnerPidsReaped([ready.middle, ready.supervisor, ready.worker]);
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership forged bootstrap and standalone guard cannot adopt or kill an external group', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const readyFile = path.join(root, 'ready');
    const guard = new URL('../test_tools/runner-group-guard.mjs', import.meta.url).pathname;
    const code = `import assert from 'node:assert/strict';import fs from 'node:fs';import {spawnSync} from 'node:child_process';${publishRunnerHandshake.toString()};
      const env={...process.env,LEXIFLOW_RUNNER_BOOTSTRAP:JSON.stringify({endpoint:'invalid',token:'invalid'}),NODE_OPTIONS:'--import='+${JSON.stringify(ownershipModule)}};
      const forged=spawnSync(process.execPath,['-e','process.exit(99)'],{env,stdio:['ignore','ignore','ignore','pipe'],timeout:1500});
      assert.equal(forged.status,1,'nonleader fails before self-group termination is installed');
      const standalone=spawnSync(process.execPath,[${JSON.stringify(guard)}],{env:{...process.env,LEXIFLOW_RUNNER_GUARD:'{"endpoint":"invalid","token":"invalid"}'},stdio:'ignore',timeout:1500});
      assert.equal(standalone.status,1);publishRunnerHandshake(${JSON.stringify(readyFile)},{sentinel:process.pid});setInterval(()=>{},1000);`;
    const sentinel = track(code);
    const ready = await waitForRunnerHandshake(readyFile);
    assert.equal(ready.sentinel, sentinel.pid);
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership root cancellation does not need a blocked middle event loop to relay signals', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const leafFile = path.join(root, 'leaf'); const readyFile = path.join(root, 'ready'); const done = path.join(root, 'done');
    const middle = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};${publishRunnerHandshake.toString()};${ownershipWaitCode}
      void runTests({args:['-e',${JSON.stringify(runnerChildScript(leafFile, path.join(root, 'beat'), path.join(root, 'worker')))}]});
      const ready=await wait(${JSON.stringify(leafFile)});publishRunnerHandshake(${JSON.stringify(readyFile)},{middle:process.pid,...ready});
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0);`;
    const code = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};const events=[];const kill=process.kill;process.kill=(pid,signal)=>{try{const r=kill(pid,signal);events.push({pid,signal,at:performance.now(),status:'present'});return r;}catch(e){events.push({pid,signal,at:performance.now(),status:e.code});throw e;}};const result=await runTests({args:['--input-type=module','-e',${JSON.stringify(middle)}]});fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({result,events}));process.exitCode=result.status==='PASS'?0:1;`;
    const sentinel = track('setInterval(()=>{},1000)'); const outer = track(code);
    const ready = await waitForRunnerHandshake(readyFile);
    outer.kill('SIGTERM');
    assert.deepEqual(await waitForExit(outer), { code: 1, signal: null });
    const evidence = JSON.parse(fs.readFileSync(done)); const { result } = evidence;
    assert.equal(result.reason, 'test process cancelled');
    assert.notEqual(result.cleanup_succeeded, false, JSON.stringify(evidence));
    await assertRunnerPidsReaped([ready.middle, ready.supervisor, ready.worker]);
    await assertSentinelAlive(sentinel.pid);
  });
});

test('ownership test-worker preload authenticates inherited group without reopening its private fd3', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const file = path.join(root, 'nested.test.mjs'); const done = path.join(root, 'done');
    writeFileSync(file, `import test from 'node:test';import assert from 'node:assert/strict';import {runTests} from ${JSON.stringify(testRunnerModule)};
      test('real Node test worker registers a nested owned group',async()=>{
        const result=await runTests({args:['-e',${JSON.stringify(`process.stdout.write(${JSON.stringify(summary())})`)}]});
        assert.equal(result.status,'PASS');assert.equal(result.checks_run,1);
      });`);
    const code = `import fs from 'node:fs';import {runTests} from ${JSON.stringify(testRunnerModule)};const result=await runTests({args:['--test','--test-reporter=tap',${JSON.stringify(file)}]});fs.writeFileSync(${JSON.stringify(done)},JSON.stringify(result));`;
    const outer = track(code); await waitForExit(outer);
    const result = JSON.parse(fs.readFileSync(done));
    assert.equal(result.status, 'PASS', JSON.stringify(result));
    assert.equal(result.checks_run, 1);
  });
});

test('ownership parent re-proves an expired failure tombstone without rewriting its first result', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const done = path.join(root, 'done');
    const code = `import assert from 'node:assert/strict';import fs from 'node:fs';import {spawn} from 'node:child_process';import {openOwnership} from ${JSON.stringify(ownershipModule)};
      const parent=await openOwnership(()=>{}),boot=JSON.parse(parent.env.LEXIFLOW_RUNNER_BOOTSTRAP);
      process.env.LEXIFLOW_RUNNER_CONTEXT=JSON.stringify({endpoint:boot.endpoint,token:boot.token});
      const leaf=await openOwnership(()=>{});delete process.env.LEXIFLOW_RUNNER_CONTEXT;
      const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'});
      const closed=new Promise(r=>child.once('close',r));
      try {
        await parent.noSpawn();await leaf.attach(child.pid);
        assert.equal(await leaf.finish(),false,'live group without guard cannot be cleaned');
        child.kill('SIGKILL');await closed;
        assert.throws(()=>process.kill(-child.pid,0),{code:'ESRCH'});
        assert.equal(await parent.finish(),true,'parent needs a fresh full-tree absence proof');
        assert.equal(await leaf.finish(),false,'first failure is immutable, not retroactively PASS');
        fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({supervisor:child.pid,result:'PASS'}));
      }finally{if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await closed;}await leaf.dispose();await parent.dispose();}`;
    const outer = track(code);
    assert.deepEqual(await waitForExit(outer), { code: 0, signal: null });
    assert.equal(JSON.parse(fs.readFileSync(done)).result, 'PASS');
  });
});

test('ownership failed live descendant still blocks the parent and receives no numeric kill', async () => {
  await ownershipFixture(async ({ root, track }) => {
    const done = path.join(root, 'done');
    const code = `import assert from 'node:assert/strict';import fs from 'node:fs';import {spawn} from 'node:child_process';import {openOwnership} from ${JSON.stringify(ownershipModule)};
      const parent=await openOwnership(()=>{}),boot=JSON.parse(parent.env.LEXIFLOW_RUNNER_BOOTSTRAP);
      process.env.LEXIFLOW_RUNNER_CONTEXT=JSON.stringify({endpoint:boot.endpoint,token:boot.token});
      const leaf=await openOwnership(()=>{});delete process.env.LEXIFLOW_RUNNER_CONTEXT;
      const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'});
      const closed=new Promise(r=>child.once('close',r));const kill=process.kill,signals=[];
      process.kill=(pid,signal)=>{signals.push({pid,signal});return kill(pid,signal)};
      try {
        await parent.noSpawn();await leaf.attach(child.pid);
        assert.equal(await leaf.finish(),false);const at=performance.now();
        assert.equal(await parent.finish(),false,'failed child is never dropped or tolerated');
        assert.ok(performance.now()-at<500,'parent barrier remains bounded, not another suite budget');
        assert.doesNotThrow(()=>process.kill(-child.pid,0));
        assert.ok(signals.length>0&&signals.every(x=>x.signal===0),'observer has no numeric kill fallback');
        fs.writeFileSync(${JSON.stringify(done)},JSON.stringify({supervisor:child.pid,result:'PASS'}));
      }finally{process.kill=kill;child.kill('SIGKILL');await closed;await leaf.dispose();await parent.dispose();}`;
    const sentinel = track('setInterval(()=>{},1000)');
    const outer = track(code);
    assert.deepEqual(await waitForExit(outer), { code: 0, signal: null });
    await assertSentinelAlive(sentinel.pid);
    assert.equal(JSON.parse(fs.readFileSync(done)).result, 'PASS');
  });
});
