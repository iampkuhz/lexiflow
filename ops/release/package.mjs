import { createHash, randomUUID } from 'node:crypto';
import { constants, createReadStream, createWriteStream, realpathSync } from 'node:fs';
import { lstat, mkdir, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildManifest, manifestSha256, serializeManifest, verifyContainedFile } from './manifest.mjs';
import { checkReleaseSource, resolveBuildIdentity, assertBuildIdentityMatches } from './version.mjs';

function reject(code) { throw new Error(code); }
async function assertNoSymlinkPath(target, allowMissing = true) {
  const absolute = path.resolve(target); let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const info = await lstat(cursor).catch((error) => error.code === 'ENOENT' && allowMissing ? null : reject('OUTPUT_PATH_INVALID'));
    if (info?.isSymbolicLink()) reject('OUTPUT_PATH_SYMLINK');
  }
}
function within(parent, child) { const rel = path.relative(parent, child); return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel)); }
async function readDescriptor(file) {
  await assertNoSymlinkPath(file, false);
  const info = await lstat(file).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.size > 2_000_000) reject('DESCRIPTOR_FILE_INVALID');
  try { return JSON.parse(await readFile(file, 'utf8')); } catch { reject('DESCRIPTOR_JSON_INVALID'); }
}

async function copyVerified(sourceRoot, relative, destinationRoot, bytesExpected, shaExpected) {
  const source = path.join(sourceRoot, ...relative.split('/'));
  const destination = path.join(destinationRoot, ...relative.split('/'));
  await mkdir(path.dirname(destination), { recursive: true });
  const digest = createHash('sha256'); let bytes = 0;
  const meter = new Transform({ transform(chunk, _encoding, callback) { bytes += chunk.length; if (bytes > bytesExpected) { callback(new Error('ARTIFACT_DIGEST_MISMATCH')); return; } digest.update(chunk); callback(null, chunk); } });
  try {
    await pipeline(createReadStream(source, { flags: constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) }), meter, createWriteStream(destination, { flags: 'wx', mode: 0o644 }));
  } catch (error) {
    if (error.message === 'ARTIFACT_DIGEST_MISMATCH') reject(error.message);
    reject('ARTIFACT_COPY_FAILED');
  }
  if (bytes !== bytesExpected || digest.digest('hex') !== shaExpected) reject('ARTIFACT_DIGEST_MISMATCH');
}

async function listFiles(root, relative = '') {
  const output = [];
  for (const name of await readdir(path.join(root, relative))) {
    const child = relative ? `${relative}/${name}` : name;
    const info = await lstat(path.join(root, child));
    if (info.isSymbolicLink()) reject('OUTPUT_INVALID');
    if (info.isDirectory()) {
      const children = await listFiles(root, child);
      if (!children.length) reject('OUTPUT_INVALID');
      output.push(...children);
    }
    else if (info.isFile()) output.push(child);
    else reject('OUTPUT_INVALID');
  }
  return output;
}

export async function packageManifest({ repoRoot, descriptorFile, artifactRoot, outputDirectory }) {
  if (![repoRoot, descriptorFile, artifactRoot, outputDirectory].every((value) => typeof value === 'string' && value.length > 0)) reject('ARGUMENTS_INVALID');
  const descriptorPath = path.resolve(descriptorFile); const root = path.resolve(artifactRoot); const output = path.resolve(outputDirectory);
  await assertNoSymlinkPath(descriptorPath, false); await assertNoSymlinkPath(root, false); await assertNoSymlinkPath(output, true);
  if (within(root, output)) reject('OUTPUT_INSIDE_ARTIFACT_ROOT');
  const descriptor = await readDescriptor(descriptorPath);
  checkReleaseSource(path.resolve(repoRoot));
  const sourceIdentity = resolveBuildIdentity(path.resolve(repoRoot));
  const manifest = await buildManifest({ repoRoot: path.resolve(repoRoot), descriptor, artifactRoot: root });
  const assertSourceUnchanged = () => {
    let current;
    try { checkReleaseSource(path.resolve(repoRoot)); current = resolveBuildIdentity(path.resolve(repoRoot)); } catch { reject('RELEASE_SOURCE_CHANGED'); }
    try { assertBuildIdentityMatches(current, sourceIdentity); } catch { reject('RELEASE_SOURCE_CHANGED'); }
  };
  assertSourceUnchanged();
  const text = serializeManifest(manifest); const checksum = manifestSha256(text);
  const expectedFiles = [...manifest.artifacts.map((item) => item.path), 'manifest.json', 'manifest.json.sha256'];
  if (new Set(expectedFiles.map((file) => file.toLocaleLowerCase('en-US'))).size !== expectedFiles.length) reject('ARTIFACT_PATH_CONFLICT');
  for (const item of manifest.artifacts) if (['manifest.json', 'manifest.json.sha256'].includes(item.path)) reject('ARTIFACT_PATH_CONFLICT');
  // Exact existing output is idempotent; any partial or differing directory remains untouched.
  const existing = await lstat(output).catch((error) => error.code === 'ENOENT' ? null : reject('OUTPUT_INVALID'));
  if (existing) {
    if (!existing.isDirectory() || existing.isSymbolicLink()) reject('OUTPUT_INVALID');
    try {
      const names = (await listFiles(output)).sort();
      if (names.join('\0') !== [...expectedFiles].sort().join('\0')) reject('OUTPUT_EXISTS_DIFFERENT');
      const old = await readFile(path.join(output, 'manifest.json'), 'utf8');
      const oldSum = await readFile(path.join(output, 'manifest.json.sha256'), 'utf8');
      if (old !== text || oldSum !== checksum) reject('OUTPUT_EXISTS_DIFFERENT');
      for (const item of manifest.artifacts) await verifyContainedFile(output, item.path, item.bytes, item.sha256);
      await verifyContainedFile(output, 'manifest.json', Buffer.byteLength(text), createHash('sha256').update(text).digest('hex'));
      await verifyContainedFile(output, 'manifest.json.sha256', Buffer.byteLength(checksum), createHash('sha256').update(checksum).digest('hex'));
      assertSourceUnchanged();
      return manifest;
    } catch { /* partial output is not reusable */ }
    reject('OUTPUT_EXISTS_DIFFERENT');
  }
  const parent = path.dirname(output); await assertNoSymlinkPath(parent, false);
  const temp = path.join(parent, `.${path.basename(output)}.manifest-${randomUUID()}`);
  try {
    await mkdir(temp, { recursive: false });
    for (const item of manifest.artifacts) await copyVerified(root, item.path, temp, item.bytes, item.sha256);
    await writeFile(path.join(temp, 'manifest.json'), text, { flag: 'wx', mode: 0o644 });
    await writeFile(path.join(temp, 'manifest.json.sha256'), checksum, { flag: 'wx', mode: 0o644 });
    // Verify every copied byte and the exact staged tree before publishing in one rename.
    const staged = await readFile(path.join(temp, 'manifest.json'), 'utf8');
    if (JSON.stringify(JSON.parse(staged)) !== JSON.stringify(manifest) || manifestSha256(staged) !== checksum) reject('STAGED_OUTPUT_INVALID');
    const stagedFiles = (await listFiles(temp)).sort();
    if (stagedFiles.join('\0') !== [...expectedFiles].sort().join('\0')) reject('STAGED_OUTPUT_INVALID');
    for (const item of manifest.artifacts) await verifyContainedFile(temp, item.path, item.bytes, item.sha256);
    await verifyContainedFile(temp, 'manifest.json', Buffer.byteLength(text), createHash('sha256').update(text).digest('hex'));
    await verifyContainedFile(temp, 'manifest.json.sha256', Buffer.byteLength(checksum), createHash('sha256').update(checksum).digest('hex'));
    assertSourceUnchanged();
    await rename(temp, output);
  } catch (error) {
    await rm(temp, { recursive: true, force: true }).catch(() => {});
    if (error.code === 'EEXIST' || error.code === 'ENOTEMPTY') reject('OUTPUT_EXISTS_DIFFERENT');
    throw error;
  }
  return manifest;
}

function parseArgs(args) {
  const names = ['--source-repo', '--descriptor-file', '--artifact-root', '--output-directory'];
  if (args.length !== names.length * 2) reject('ARGUMENTS_INVALID');
  const result = {};
  for (let i = 0; i < names.length; i += 1) {
    if (args[i * 2] !== names[i] || !args[i * 2 + 1] || args[i * 2 + 1].startsWith('--')) reject('ARGUMENTS_INVALID');
    result[['repoRoot', 'descriptorFile', 'artifactRoot', 'outputDirectory'][i]] = args[i * 2 + 1];
  }
  return result;
}
function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) Promise.resolve().then(() => packageManifest(parseArgs(process.argv.slice(2)))).then(() => process.stdout.write('release manifest assembled\n')).catch(() => { process.stderr.write('release manifest rejected\n'); process.exitCode = 1; });
