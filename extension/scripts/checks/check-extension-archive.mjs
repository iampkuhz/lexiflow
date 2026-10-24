import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readExtensionFiles } from '../../../ops/release/integrity/extension-archive-integrity.mjs';
import { resolveBuildIdentity } from '../../../ops/release/integrity/source-build-identity.mjs';
import { EXTENSION_PACKAGE_FILES, validateExtensionArchiveEntries } from '../packaging/extension-archive-contract.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
function fail(code) { throw new Error(code); }

async function stableRead(target, limit) {
  const fd = await open(target, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await fd.stat();
    if (!before.isFile() || before.size <= 0 || before.size > limit) fail('ARCHIVE_INVALID');
    const bytes = await fd.readFile();
    const after = await fd.stat();
    if (bytes.length !== before.size || ['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs'].some(key => before[key] !== after[key])) fail('ARCHIVE_CHANGED');
    return bytes;
  } finally { await fd.close(); }
}

/** Verify downloaded ZIP, adjacent checksum and current complete source identity. */
export async function checkExtensionArchive(zipPath, expectedCommit, repoRoot = ROOT, options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)
      || Object.keys(options).some(key => !['mode', 'verifyCurrentIdentity'].includes(key))) fail('ARCHIVE_ARGUMENTS_INVALID');
  const { mode = 'formal', verifyCurrentIdentity = true } = options;
  if (!['formal', 'development'].includes(mode) || typeof verifyCurrentIdentity !== 'boolean') fail('ARCHIVE_ARGUMENTS_INVALID');
  const apiPort = mode === 'development' ? 18081 : 18080;
  if (typeof zipPath !== 'string' || !zipPath || typeof expectedCommit !== 'string' || !/^[a-f0-9]{40,64}$/.test(expectedCommit)) fail('ARCHIVE_ARGUMENTS_INVALID');
  const target = path.resolve(zipPath);
  const bytes = await stableRead(target, 256 * 1024 * 1024);
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  const filename = path.basename(target);
  const checksumPath = `${target}.sha256`;
  const sidecar = (await stableRead(checksumPath, 512)).toString('utf8');
  if (sidecar !== `${sha256}  ${filename}\n`) fail('ARCHIVE_CHECKSUM_MISMATCH');
  let expected;
  if (verifyCurrentIdentity) {
    const head = execFileSync('git', ['-C', repoRoot, 'rev-parse', '--verify', 'HEAD^{commit}'], { encoding: 'utf8' }).trim();
    if (head !== expectedCommit) fail('ARCHIVE_EXPECTED_COMMIT_MISMATCH');
    expected = resolveBuildIdentity(repoRoot);
    if (expected.sourceCommit !== expectedCommit) fail('ARCHIVE_SOURCE_IDENTITY_MISMATCH');
  }
  const entries = await readExtensionFiles(target, sha256);
  if (mode === 'development' && entries.has('expected-dataset.json')) fail('ARCHIVE_MODE_MISMATCH');
  const names = [...entries.keys()].sort();
  const expectedNames = mode === 'development'
    ? EXTENSION_PACKAGE_FILES.filter(name => name !== 'expected-dataset.json') : EXTENSION_PACKAGE_FILES;
  if (JSON.stringify(names) !== JSON.stringify([...expectedNames].sort())) fail('ARCHIVE_FILE_SET_MISMATCH');
  const { manifest, identity, dataset } = validateExtensionArchiveEntries(entries, { buildIdentity: expected, apiPort, filename });
  if (identity.sourceCommit !== expectedCommit) fail('ARCHIVE_SOURCE_IDENTITY_MISMATCH');
  return { filename, sha256, bytes: bytes.length, buildIdentity: identity, dataset, mode, entries };
}

function invokedDirectly() { try { return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url); } catch { return false; } }
if (invokedDirectly()) {
  const args = process.argv.slice(2);
  const development = args[0] === '--development';
  const values = development ? args.slice(1) : args;
  if (values.length !== 2) { process.stderr.write('usage: node extension/scripts/checks/check-extension-archive.mjs [--development] <zip-path> <expected-commit>\n'); process.exitCode = 2; }
  else checkExtensionArchive(values[0], values[1], ROOT, { mode: development ? 'development' : 'formal' }).then(result => process.stdout.write(`${JSON.stringify({ filename: result.filename, sha256: result.sha256, bytes: result.bytes, buildIdentity: result.buildIdentity, mode: result.mode })}\n`)).catch(error => { process.stderr.write(`extension archive rejected: ${error.message}\n`); process.exitCode = 1; });
}
