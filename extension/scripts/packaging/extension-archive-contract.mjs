import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { lstat, mkdir, readFile, writeFile, rm, link } from 'node:fs/promises';
import path from 'node:path';
import { chromeVersion, validateBuildIdentity, assertBuildIdentityMatches } from '../../../ops/release/integrity/source-build-identity.mjs';
import { validateExpectedDataset } from '../build/expected-dataset.mjs';

export const EXTENSION_PACKAGE_FILES = ['manifest.json', 'build-identity.json', 'expected-dataset.json', 'background.js', 'content.js', 'youtube-bridge.js', 'popup.js', 'popup.html', 'popup.css', 'assets/icon-16.png', 'assets/icon-32.png', 'assets/icon-48.png', 'assets/icon-128.png', 'assets/logo.svg'];
const fail = code => { throw new Error(code); };
const sha = value => createHash('sha256').update(value).digest('hex');
function stableFileBytes(file, expectedLength, named = fs.lstatSync(file)) {
  if (named.isSymbolicLink() || !named.isFile() || named.size > expectedLength) fail('EXTENSION_TARGET_EXISTS');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.dev !== named.dev || opened.ino !== named.ino || opened.size > expectedLength) fail('EXTENSION_TARGET_EXISTS');
    const bytes = fs.readFileSync(fd), after = fs.fstatSync(fd), current = fs.lstatSync(file);
    if (after.size !== opened.size || after.mtimeMs !== opened.mtimeMs || after.ctimeMs !== opened.ctimeMs
        || current.isSymbolicLink() || current.dev !== opened.dev || current.ino !== opened.ino) fail('EXTENSION_TARGET_EXISTS');
    return bytes;
  } finally { fs.closeSync(fd); }
}
export function readStableRegularFile(file, maxBytes) {
  const named = fs.lstatSync(file);
  if (named.isSymbolicLink() || !named.isFile() || named.size < 1 || named.size > maxBytes) fail('EXTENSION_ARCHIVE_INVALID');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0) | (fs.constants.O_NONBLOCK ?? 0));
  try {
    const before = fs.fstatSync(fd);
    if (!before.isFile() || before.dev !== named.dev || before.ino !== named.ino || before.size > maxBytes) fail('EXTENSION_ARCHIVE_INVALID');
    const bytes = fs.readFileSync(fd), after = fs.fstatSync(fd), current = fs.lstatSync(file);
    if (bytes.length !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs
        || current.isSymbolicLink() || current.dev !== before.dev || current.ino !== before.ino) fail('EXTENSION_ARCHIVE_INVALID');
    return bytes;
  } finally { fs.closeSync(fd); }
}

export function validateExtensionArchiveEntries(entries, { buildIdentity, expectedDataset, apiPort = 18080, filename } = {}) {
  if (![18080, 18081].includes(apiPort)) fail('EXTENSION_API_PORT_INVALID');
  const names = [...entries.keys()].sort();
  const filesForPort = apiPort === 18081 && !expectedDataset && !entries.has('expected-dataset.json')
    ? EXTENSION_PACKAGE_FILES.filter(name => name !== 'expected-dataset.json') : EXTENSION_PACKAGE_FILES;
  if (JSON.stringify(names) !== JSON.stringify([...filesForPort].sort())) fail('ARCHIVE_FILE_SET_MISMATCH');
  let identity, manifest;
  try { identity = JSON.parse(entries.get('build-identity.json').toString('utf8')); manifest = JSON.parse(entries.get('manifest.json').toString('utf8')); validateBuildIdentity(identity); }
  catch { fail('ARCHIVE_IDENTITY_MISMATCH'); }
  if (buildIdentity) { try { assertBuildIdentityMatches(identity, buildIdentity); } catch { fail('EXTENSION_IDENTITY_MISMATCH'); } }
  let dataset = null;
  if (entries.has('expected-dataset.json')) {
    try { dataset = validateExpectedDataset(JSON.parse(entries.get('expected-dataset.json').toString('utf8'))); }
    catch { fail('EXPECTED_DATASET_INVALID'); }
  }
  if (expectedDataset && JSON.stringify(dataset) !== JSON.stringify(validateExpectedDataset(expectedDataset))) fail('EXPECTED_DATASET_INVALID');
  if (filename && filename !== `lexiflow-extension-${identity.softwareVersion}.zip`) fail('ARCHIVE_FILENAME_MISMATCH');
  if (manifest.manifest_version !== 3 || manifest.version !== chromeVersion(identity.softwareVersion) || manifest.version_name !== identity.softwareVersion
      || JSON.stringify(manifest.permissions) !== '["storage"]'
      || JSON.stringify(manifest.host_permissions) !== JSON.stringify([`http://127.0.0.1:${apiPort}/*`])
      || manifest.background?.service_worker !== 'background.js' || manifest.action?.default_popup !== 'popup.html') fail('ARCHIVE_MANIFEST_INVALID');
  const files = new Set(names);
  const refs = [manifest.background.service_worker, manifest.action.default_popup,
    ...Object.values(manifest.action.default_icon ?? {}), ...Object.values(manifest.icons ?? {}),
    ...(manifest.content_scripts ?? []).flatMap(item => item.js ?? []), ...(manifest.content_scripts ?? []).flatMap(item => item.css ?? [])];
  for (const ref of refs) if (!files.has(ref)) fail('ARCHIVE_RESOURCE_MISSING');
  const htmlRefs = [...entries.get('popup.html').toString('utf8').matchAll(/(?:src|href)=["']([^"']+)["']/gu)].map(([, ref]) => ref);
  if (!htmlRefs.length) fail('ARCHIVE_MANIFEST_INVALID');
  for (const ref of htmlRefs) if (!/^(?:https?:|data:|#)/iu.test(ref) && !files.has(ref)) fail('ARCHIVE_RESOURCE_MISSING');
  const cssRefs = [...entries.get('popup.css').toString('utf8').matchAll(/url\(["']?([^"')]+)["']?\)|@import\s+["']([^"']+)["']/giu)];
  for (const match of cssRefs) { const ref = match[1] ?? match[2]; if (!/^(?:https?:|data:|#)/iu.test(ref) && !files.has(ref)) fail('ARCHIVE_RESOURCE_MISSING'); }
  return { identity, manifest, dataset };
}

export function assertExtensionTreeMatches(directory, entries) {
  const expected = new Set(entries.keys()), observed = new Set();
  const expectedDirs = new Set();
  for (const name of expected) { const parts = name.split('/'); for (let i = 1; i < parts.length; i++) expectedDirs.add(parts.slice(0, i).join('/')); }
  function visit(relative = '') {
    const target = relative ? path.join(directory, ...relative.split('/')) : directory;
    const info = fs.lstatSync(target);
    if (info.isSymbolicLink() || !info.isDirectory()) fail('EXTENSION_ARCHIVE_TREE_MISMATCH');
    if (relative && !expectedDirs.has(relative)) fail('EXTENSION_ARCHIVE_TREE_MISMATCH');
    for (const name of fs.readdirSync(target).sort()) {
      const child = relative ? `${relative}/${name}` : name, childPath = path.join(directory, ...child.split('/'));
      const childInfo = fs.lstatSync(childPath);
      if (childInfo.isDirectory()) visit(child);
      else if (childInfo.isFile() && expected.has(child)) {
        let actual;
        try { actual = stableFileBytes(childPath, entries.get(child).length, childInfo); }
        catch { fail('EXTENSION_ARCHIVE_TREE_MISMATCH'); }
        if (!actual.equals(entries.get(child))) fail('EXTENSION_ARCHIVE_TREE_MISMATCH');
        observed.add(child);
      } else fail('EXTENSION_ARCHIVE_TREE_MISMATCH');
    }
  }
  visit();
  if (observed.size !== expected.size) fail('EXTENSION_ARCHIVE_TREE_MISMATCH');
}

export function materializeExtensionTree(directory, entries) {
  const expected = new Set(entries.keys()), expectedDirs = new Set(), observed = new Map(), observedDirs = new Set();
  for (const name of expected) { const parts = name.split('/'); for (let i = 1; i < parts.length; i++) expectedDirs.add(parts.slice(0, i).join('/')); }
  if (fs.existsSync(directory)) {
    function visit(relative = '') {
      const target = relative ? path.join(directory, ...relative.split('/')) : directory;
      const info = fs.lstatSync(target);
      if (info.isSymbolicLink() || !info.isDirectory()) fail('EXTENSION_TARGET_EXISTS');
      if (relative) { if (!expectedDirs.has(relative)) fail('EXTENSION_TARGET_EXISTS'); observedDirs.add(relative); }
      for (const name of fs.readdirSync(target).sort()) {
        const child = relative ? `${relative}/${name}` : name, childPath = path.join(directory, ...child.split('/')), childInfo = fs.lstatSync(childPath);
        if (childInfo.isSymbolicLink()) fail('EXTENSION_TARGET_EXISTS');
        if (childInfo.isDirectory()) visit(child);
        else if (childInfo.isFile() && expected.has(child)) {
          const targetBytes = entries.get(child), bytes = stableFileBytes(childPath, targetBytes.length, childInfo);
          if (bytes.length > targetBytes.length || !targetBytes.subarray(0, bytes.length).equals(bytes)) fail('EXTENSION_TARGET_EXISTS');
          observed.set(child, bytes);
        } else fail('EXTENSION_TARGET_EXISTS');
      }
    }
    visit();
    if (observed.size === expected.size && [...expected].every(name => observed.get(name)?.equals(entries.get(name)))) return;
    for (const name of observed.keys()) fs.unlinkSync(path.join(directory, ...name.split('/')));
    for (const name of [...observedDirs].sort((a, b) => b.split('/').length - a.split('/').length)) fs.rmdirSync(path.join(directory, ...name.split('/')));
    fs.rmdirSync(directory);
  }
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  for (const [name, content] of entries) {
    const target = path.join(directory, ...name.split('/'));
    fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
    fs.writeFileSync(target, content, { mode: 0o600, flag: 'wx' });
  }
}

export async function publishExtensionArchive(zipBytes, checksum, directory, filename) {
  if (ArrayBuffer.isView(zipBytes)) zipBytes = Buffer.from(zipBytes.buffer, zipBytes.byteOffset, zipBytes.byteLength);
  if (typeof filename !== 'string' || !/^lexiflow-extension-[0-9A-Za-z][0-9A-Za-z.-]*\.zip$/.test(filename)
      || path.basename(filename) !== filename || !Buffer.isBuffer(zipBytes) || typeof checksum !== 'string'
      || checksum !== `${sha(zipBytes)}  ${filename}\n`) fail('ARCHIVE_PUBLICATION_INVALID');
  await assertNoSymlinkPath(directory); await mkdir(directory, { recursive: true });
  const zipPath = path.join(directory, filename), sumPath = `${zipPath}.sha256`;
  await assertNoSymlinkPath(zipPath); await assertNoSymlinkPath(sumPath);
  const existingZip = await readExistingRegularFile(zipPath), existingSum = await readExistingRegularFile(sumPath);
  if (existingZip || existingSum) {
    if (existingZip?.equals(zipBytes) && existingSum?.toString() === checksum) return;
    fail('different release artifact already exists');
  }
  const nonce = `${process.pid}-${Math.random().toString(16).slice(2)}`, tmpZip = `${zipPath}.${nonce}.tmp`, tmpSum = `${sumPath}.${nonce}.tmp`;
  try {
    await writeFile(tmpZip, zipBytes, { flag: 'wx' }); await writeFile(tmpSum, checksum, { flag: 'wx' }); await link(tmpZip, zipPath);
    try { await link(tmpSum, sumPath); } catch (error) { await rm(zipPath, { force: true }); throw error; }
  } finally { await rm(tmpZip, { force: true }); await rm(tmpSum, { force: true }); }
}
async function readExistingRegularFile(target) { try { const st = await lstat(target); if (!st.isFile() || st.isSymbolicLink()) fail('release artifact path is not a regular file'); return await readFile(target); } catch (e) { if (e.code === 'ENOENT') return null; throw e; } }
async function assertNoSymlinkPath(target) { let current = path.parse(target).root; for (const part of target.slice(current.length).split(path.sep).filter(Boolean)) { current = path.join(current, part); const info = await lstat(current).catch(() => null); if (info?.isSymbolicLink()) fail('release output path contains symlink'); } }
