import { execFileSync } from 'node:child_process';
import { readFileSync, realpathSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function parseVersion(raw) {
  const value = raw.endsWith('\r\n') ? raw.slice(0, -2) : raw.endsWith('\n') ? raw.slice(0, -1) : raw;
  if (!/^(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})$/.test(value)) throw new Error('invalid software version');
  const parts = value.split('.').map(Number);
  if (parts.some((part) => part > 65535) || parts.every((part) => part === 0)) throw new Error('invalid software version');
  return value;
}

export async function readVersion(source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'version.txt')) {
  try { return parseVersion(await readFile(source, 'utf8')); }
  catch { throw new Error('software version missing or invalid'); }
}

export function checkReleaseSource(repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')) {
  const git = (...args) => execFileSync('git', ['-C', repoRoot, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 10_000, maxBuffer: 100_000 }).trim();
  let root;
  try { root = git('rev-parse', '--show-toplevel'); }
  catch { throw new Error('release input is not a Git repository'); }
  try { if (realpathSync(root) !== realpathSync(repoRoot)) throw new Error('release input must be repository root'); }
  catch (error) { if (error.message === 'release input must be repository root') throw error; throw new Error('release input must be repository root'); }
  let softwareVersion;
  try { softwareVersion = parseVersion(readFileSync(path.join(repoRoot, 'ops/release/version.txt'), 'utf8')); }
  catch { throw new Error('software version missing or invalid'); }
  let sourceCommit;
  try { sourceCommit = git('rev-parse', '--verify', 'HEAD^{commit}'); }
  catch { throw new Error('release input lacks valid HEAD'); }
  if (!/^[0-9a-f]{40,64}$/.test(sourceCommit)) throw new Error('release input lacks valid HEAD');
  let status;
  try { status = git('status', '--porcelain=v1', '--untracked-files=all'); }
  catch { throw new Error('release input status unavailable'); }
  if (status) throw new Error('release input must be clean');
  return { softwareVersion, sourceCommit };
}

async function main(args) {
  if (args.length === 0) { console.log(JSON.stringify({ softwareVersion: await readVersion(), sourceCommit: null })); return; }
  if (args.length !== 1 || args[0] !== '--release') throw new Error('invalid arguments');
  console.log(JSON.stringify(checkReleaseSource()));
}

function invokedDirectly() {
  try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (invokedDirectly()) {
  main(process.argv.slice(2)).catch(() => { process.stderr.write('release input rejected\n'); process.exitCode = 1; });
}
