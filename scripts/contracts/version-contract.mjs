import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { encodeLock, LOCK_PATH, parseLock, readLock, SCOPES } from './contract-lock.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const sha = value => createHash('sha256').update(value).digest('hex');
const git = (...args) => execFileSync('git', ['-C', root, ...args], {
  encoding: 'utf8', timeout: 10000, maxBuffer: 16384, stdio: ['ignore', 'pipe', 'pipe'],
});

export function nextRecord(base, sha256) {
  const version = base.version + Number(base.sha256 !== sha256);
  if (version > 999999999) throw new Error('CONTRACT_VERSION_OVERFLOW');
  return { version, sha256 };
}

const equal = (a,b) => a.version === b.version && a.sha256 === b.sha256;
export async function current(scope) {
  if (scope === 'extension-api') {
    const { extensionContract } = await import('./extension-contract.mjs');
    return sha(JSON.stringify(extensionContract(root)));
  }
  if (scope !== 'sqlite-storage') throw new Error('CONTRACT_SCOPE_INVALID');
  const env = { ...process.env, ...(process.env.LEXIFLOW_JAVA_HOME ? { JAVA_HOME: process.env.LEXIFLOW_JAVA_HOME } : {}) };
  const output = execFileSync(join(root, 'backend/gradlew'), ['-p', 'backend', '--quiet', ':api:storageContract'],
    { cwd: root, encoding: 'utf8', env, timeout: 120000, maxBuffer: 64 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
  const value = JSON.parse(output);
  if (!/^[0-9a-f]{64}$/u.test(value.sha256)) throw new Error('STORAGE_CONTRACT_OUTPUT_INVALID');
  return value.sha256;
}

function update(lock, scope, computed) {
  const path = join(root, LOCK_PATH), mutex = path + '.write-lock';
  mkdirSync(mutex, { mode: 0o700 });
  try {
    if (encodeLock(readLock(path)) !== encodeLock(lock)) throw new Error('CONTRACT_LOCK_CHANGED');
    const next = join(mutex, 'next');
    writeFileSync(next, encodeLock({ ...lock, [scope]: computed }), { flag: 'wx', mode: 0o644 });
    renameSync(next, path);
  } finally { rmSync(mutex, { recursive: true }); }
}

async function main(args) {
  const [action, scopeFlag, scope, baseFlag, baseRef, jsonFlag] = args;
  const onlyCurrent = action === 'verify';
  const json = onlyCurrent ? baseFlag === '--json' : jsonFlag === '--json';
  if (!['check', 'sync', 'verify'].includes(action) || scopeFlag !== '--scope' || !SCOPES.includes(scope) ||
      (!onlyCurrent && (baseFlag !== '--base' || !baseRef || args.length !== (json ? 6 : 5))) ||
      (onlyCurrent && args.length !== (json ? 4 : 3))) {
    throw new Error('usage: version-contract.mjs check|sync --scope <scope> --base <Git ref> [--json]; verify --scope <scope> [--json]');
  }
  const lock = readLock();
  let base;
  if (!onlyCurrent) {
    try {
      const revision = git('rev-parse', '--verify', '--end-of-options', baseRef + '^{commit}').trim();
      if (!/^[0-9a-f]{40,64}$/u.test(revision)) throw new Error('base invalid');
      base = { revision, ...parseLock(git('show', revision + ':' + LOCK_PATH))[scope] };
    } catch { throw new Error('CONTRACT_BASELINE_UNAVAILABLE'); }
  }
  const now = await current(scope);
  const recorded = lock[scope];
  const computed = base ? nextRecord(base, now) : { version: recorded.version, sha256: now };
  const changed = !equal(recorded, computed);
  if (action === 'sync' && changed) update(lock, scope, computed);
  const status = !changed || action === 'sync' ? 'PASS' : 'FAIL';
  const decision = base && computed.version !== base.version ? 'bump' : 'unchanged';
  const report = { status, scope, base: base ?? null, recorded, computed, decision };
  console.log(json ? JSON.stringify(report) : [status, scope + ':', base ? 'v' + base.version + ' →' : '', 'v' + computed.version,
    changed ? (action === 'sync' ? '已写入' : '记录不符，请检查变更并运行 sync') : '记录一致'].filter(Boolean).join(' '));
  return status === 'PASS' ? 0 : 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try { process.exitCode = await main(process.argv.slice(2)); }
  catch (error) {
    console.error('BLOCKED ' + error.message.split('\n')[0]);
    process.exitCode = 2;
  }
}
