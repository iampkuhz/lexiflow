import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readExtensionFiles } from '../../ops/release/archive-identity.mjs';
import { resolveBuildIdentity, assertBuildIdentityMatches, validateBuildIdentity, chromeVersion } from '../../ops/release/version.mjs';
import { PACKAGE_FILES } from './release.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
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
export async function checkExtensionArchive(zipPath, expectedCommit, repoRoot = ROOT, { verifyCurrentIdentity = true } = {}) {
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
  const names = [...entries.keys()].sort();
  if (JSON.stringify(names) !== JSON.stringify([...PACKAGE_FILES].sort())) fail('ARCHIVE_FILE_SET_MISMATCH');
  let manifest, identity;
  try {
    manifest = JSON.parse(entries.get('manifest.json').toString('utf8'));
    identity = JSON.parse(entries.get('build-identity.json').toString('utf8'));
    validateBuildIdentity(identity);
    if (identity.sourceCommit !== expectedCommit) fail('ARCHIVE_SOURCE_IDENTITY_MISMATCH');
    if (expected) assertBuildIdentityMatches(identity, expected);
  } catch { fail('ARCHIVE_IDENTITY_MISMATCH'); }
  if (filename !== `lexiflow-extension-${identity.softwareVersion}.zip`) fail('ARCHIVE_FILENAME_MISMATCH');
  if (manifest.manifest_version !== 3 || manifest.version !== chromeVersion(identity.softwareVersion) || manifest.version_name !== identity.softwareVersion
    || JSON.stringify(manifest.permissions) !== '["storage"]'
    || JSON.stringify(manifest.host_permissions) !== '["http://127.0.0.1:18080/*"]'
    || manifest.background?.service_worker !== 'background.js' || manifest.action?.default_popup !== 'popup.html') fail('ARCHIVE_MANIFEST_INVALID');
  const files = new Set(names);
  const refs = [manifest.background.service_worker, manifest.action.default_popup,
    ...Object.values(manifest.action.default_icon ?? {}), ...Object.values(manifest.icons ?? {}),
    ...(manifest.content_scripts ?? []).flatMap(item => item.js ?? []), ...(manifest.content_scripts ?? []).flatMap(item => item.css ?? [])];
  for (const ref of refs) if (!files.has(ref)) fail('ARCHIVE_RESOURCE_MISSING');
  const html = entries.get('popup.html').toString('utf8');
  const htmlRefs = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/gu)].map(([, ref]) => ref);
  if (htmlRefs.length === 0) fail('ARCHIVE_MANIFEST_INVALID');
  for (const ref of htmlRefs) {
    if (/^(?:https?:|data:|#)/iu.test(ref)) continue;
    if (!files.has(ref)) fail('ARCHIVE_RESOURCE_MISSING');
  }
  const css = entries.get('popup.css').toString('utf8');
  const cssRefs = [...css.matchAll(/url\(["']?([^"')]+)["']?\)|@import\s+["']([^"']+)["']/giu)];
  for (const match of cssRefs) {
    const ref = match[1] ?? match[2];
    if (/^(?:https?:|data:|#)/iu.test(ref)) continue;
    if (!files.has(ref)) fail('ARCHIVE_RESOURCE_MISSING');
  }
  return { filename, sha256, bytes: bytes.length, buildIdentity: identity, entries };
}

function invokedDirectly() { try { return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url); } catch { return false; } }
if (invokedDirectly()) {
  const args = process.argv.slice(2);
  if (args.length !== 2) { process.stderr.write('usage: node extension/scripts/archive-check.mjs <zip-path> <expected-commit>\n'); process.exitCode = 2; }
  else checkExtensionArchive(args[0], args[1]).then(result => process.stdout.write(`${JSON.stringify({ filename: result.filename, sha256: result.sha256, bytes: result.bytes, buildIdentity: result.buildIdentity })}\n`)).catch(error => { process.stderr.write(`extension archive rejected: ${error.message}\n`); process.exitCode = 1; });
}
