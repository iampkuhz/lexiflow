import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, unlinkSync } from 'node:fs';
import path from 'node:path';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { Worker, isMainThread, workerData } from 'node:worker_threads';
import { resolveBuildIdentity, resolveCleanBuildIdentity } from '../integrity/source-build-identity.mjs';

const git = (cwd, ...args) => childProcess.execFileSync('git', args, { cwd, stdio: 'ignore', timeout: 10_000, env: { ...process.env, GIT_AUTHOR_DATE: '2026-10-05T00:00:00Z', GIT_COMMITTER_DATE: '2026-10-05T18:00:00Z' } });

// 独立状态保留全部真实 Git、生产接口和断言；Worker 隔离 builtin mock。
function assertOracleState(root, state) {
  if (state === 'staged') { writeFileSync(path.join(root, 'source'), 'staged'); git(root, 'add', 'source'); }
  if (state === 'unstaged') writeFileSync(path.join(root, 'source'), 'unstaged');
  if (state === 'untracked') writeFileSync(path.join(root, 'new-file'), 'new');
  if (state === 'head') { git(root, 'commit', '--allow-empty', '-qm', 'second'); }
  const original = childProcess.execFileSync;
  let oracle = false;
  const actualCommands = [];
  const counts = { combined: 0, show: 0, status: 0, lsFiles: 0 };
  childProcess.execFileSync = function (command, args, options) {
    if (!oracle && command === 'git') actualCommands.push(args.slice(2));
    if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) {
      counts.combined++;
      if (!oracle) return original.call(this, command, args, options);
      const top = original.call(this, command, ['-C', root, 'rev-parse', '--show-toplevel'], options);
      const head = original.call(this, command, ['-C', root, 'rev-parse', '--verify', 'HEAD^{commit}'], options);
      return `${top}${head}`;
    }
    if (command === 'git' && args[2] === 'show') counts.show++;
    if (command === 'git' && args[2] === 'status') counts.status++;
    if (command === 'git' && args[2] === 'ls-files') counts.lsFiles++;
    return original.call(this, command, args, options);
  };
  syncBuiltinESMExports();
  try {
    const identity = resolveBuildIdentity(root);
    assert.equal(actualCommands.length, 16, `${state} physical Git commands`);
    assert.deepEqual(counts, { combined: 4, show: 4, status: 4, lsFiles: 4 });
    oracle = true;
    assert.deepEqual(resolveBuildIdentity(root), identity, `${state} identity oracle`);
    assert.deepEqual(counts, { combined: 8, show: 8, status: 8, lsFiles: 8 });
  } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
}

function assertCleanState(root, state) {
  if (state === 'clean') {
    mkdirSync(path.join(root, 'ignored')); writeFileSync(path.join(root, 'ignored/private'), 'ignored');
    const original = childProcess.execFileSync;
    const counts = { combined: 0, show: 0, status: 0, lsFiles: 0 };
    let expectedBuffer = 100_000;
    childProcess.execFileSync = function (command, args, options) {
      if (command === 'git' && args.includes('--show-toplevel') && args.includes('--verify')) counts.combined++;
      if (command === 'git' && args[2] === 'show') counts.show++;
      if (command === 'git' && args[2] === 'status') {
        counts.status++;
        assert.equal(options.maxBuffer, expectedBuffer);
      }
      if (command === 'git' && args[2] === 'ls-files') counts.lsFiles++;
      return original.call(this, command, args, options);
    };
    syncBuiltinESMExports();
    try {
      const clean = resolveCleanBuildIdentity(root);
      assert.equal(clean.channel, 'release');
      assert.equal(clean.dirty, false);
      assert.deepEqual(counts, { combined: 4, show: 4, status: 4, lsFiles: 4 });
      expectedBuffer = 8 * 1024 * 1024;
      assert.deepEqual(resolveBuildIdentity(root), clean, 'ordinary resolver retains its existing status bound');
      assert.deepEqual(counts, { combined: 8, show: 8, status: 8, lsFiles: 8 });
    } finally { childProcess.execFileSync = original; syncBuiltinESMExports(); }
  } else if (state === 'snapshot') {
    writeFileSync(path.join(root, 'ops/release/version.txt'), '1.2.3-SNAPSHOT\n');
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'snapshot');
    assert.equal(resolveCleanBuildIdentity(root).channel, 'snapshot');
  } else {
    if (state === 'staged') { writeFileSync(path.join(root, 'source'), 'dirty'); git(root, 'add', 'source'); }
    if (state === 'unstaged') writeFileSync(path.join(root, 'source'), 'dirty');
    if (state === 'untracked') writeFileSync(path.join(root, 'new-file'), 'dirty');
    if (state === 'deleted') unlinkSync(path.join(root, 'source'));
    assert.equal(resolveBuildIdentity(root).dirty, true, `${state}: ordinary identity supports dirty trees`);
    assert.throws(() => resolveCleanBuildIdentity(root), /release input must be clean/, state);
  }
}

/** 全部 Worker 退出后才返回或抛错，由调用者清理其独占 fixture。 */
export async function runVersionStates(cases, kind) {
  let next = 0;
  async function drain() {
    while (next < cases.length) {
      const { root, state } = cases[next++];
      await new Promise((resolve, reject) => {
        const worker = new Worker(new URL(import.meta.url), { workerData: { root, state, kind } });
        let error;
        worker.once('error', failure => { error = failure; });
        worker.once('exit', code => {
          if (error || code !== 0) reject(error ?? new Error(`version fixture worker exited ${code}: ${kind}/${state}`));
          else resolve();
        });
      });
    }
  }
  // 并发只在本用例独立状态之间；包括准备/退出/清理的套件总预算不变。
  const results = await Promise.allSettled(Array.from({ length: Math.min(3, cases.length) }, drain));
  const failure = results.find(result => result.status === 'rejected');
  if (failure) throw failure.reason;
}

if (!isMainThread) {
  if (workerData.kind === 'oracle') assertOracleState(workerData.root, workerData.state);
  else if (workerData.kind === 'clean') assertCleanState(workerData.root, workerData.state);
  else throw new Error('unknown version fixture worker');
}
