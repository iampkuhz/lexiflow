#!/usr/bin/env node
// 固定合成候选事务；不替代公开 pipeline 或正式发行许可证明。
import { createHash } from 'node:crypto';
import { constants, realpathSync } from 'node:fs';
import { lstat, open, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCandidateImages } from '../build/release-image-builder.mjs';
import { assembleReleaseCandidate } from '../build/release-candidate-assembler.mjs';
import { verifyReleaseCandidate } from '../integrity/release-candidate-verifier.mjs';
import { resolveCleanBuildIdentity } from '../integrity/source-build-identity.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const HEX = /^[a-f0-9]{64}$/;
const sha = data => createHash('sha256').update(data).digest('hex');
const fail = () => { throw new Error('SYNTHETIC_INPUT_INVALID'); };
const exact = (v, keys) => v && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).sort().join('\0') === [...keys].sort().join('\0');
const within = (root, file) => { const relative = path.relative(root, file);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative); };

export function validateSyntheticInput(input) {
  if (!exact(input, ['scratchDirectory', 'endpoint', 'datasets'])
    || typeof input.scratchDirectory !== 'string' || !path.isAbsolute(input.scratchDirectory)
    || path.resolve(input.scratchDirectory) !== input.scratchDirectory
    || /[\u0000-\u001f\u007f]/.test(input.scratchDirectory)
    || typeof input.endpoint !== 'string' || !/^unix:\/\/\/[^\s?#\u0000-\u001f\u007f]+$/.test(input.endpoint)
    || !Array.isArray(input.datasets) || input.datasets.length !== 2) fail();
  for (const [i, row] of input.datasets.entries()) {
    if (!exact(row, ['file', 'bytes', 'sha256', 'datasetVersion', 'preparationPolicy'])
      || row.file !== ['dataset-a.zip', 'dataset-b.zip'][i]
      || !Number.isSafeInteger(row.bytes) || row.bytes < 1 || row.bytes > 64 * 1024 * 1024
      || typeof row.sha256 !== 'string' || !HEX.test(row.sha256)
      || !Number.isSafeInteger(row.datasetVersion) || row.datasetVersion < 1
      || row.preparationPolicy !== 'lexiflow.deterministic-preparation.v1') fail();
  }
  if (input.datasets[0].sha256 === input.datasets[1].sha256
    || input.datasets[0].datasetVersion === input.datasets[1].datasetVersion) fail();
  return input;
}

async function safePath(file, directory = false) {
  let cursor = path.parse(file).root;
  for (const part of file.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    if ((await lstat(cursor)).isSymbolicLink()) fail();
  }
  const info = await lstat(file);
  if (directory ? !info.isDirectory() : !info.isFile()) fail();
}
async function bytes(file, maximum) {
  await safePath(file);
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
  try {
    const first = await handle.stat();
    if (!first.isFile() || first.size < 1 || first.size > maximum) fail();
    const data = await handle.readFile(); const last = await handle.stat();
    if (data.length !== first.size || data.length > maximum
      || [first.dev, first.ino, first.size, first.mtimeMs, first.ctimeMs].join(':')
        !== [last.dev, last.ino, last.size, last.mtimeMs, last.ctimeMs].join(':')) fail();
    return data;
  } finally { await handle.close(); }
}
const fact = data => ({ bytes: data.length, sha256: sha(data) });

export function syntheticDescriptor(identity, records, licenses, dataset, sqlSha) {
  const selected = { releaseId: `synthetic-${dataset.sha256}`,
    preparationId: `synthetic-${sha(dataset.preparationPolicy)}`, ruleId: 'synthetic-no-model' };
  return { schemaVersion: 1, buildIdentity: identity, softwareVersion: identity.softwareVersion,
    sourceCommit: identity.sourceCommit, apiContract: 'caption-hints.v2', sqlVersion: sqlSha,
    dataset: selected, platforms: ['linux/arm64'], licenses,
    artifacts: [...records, { role: 'dataset', path: `dataset/${dataset.file}`,
      bytes: dataset.bytes, sha256: dataset.sha256, licenseIds: ['dataset-license'],
      metadata: { ...selected, sqlVersion: sqlSha } }] };
}

export function syntheticLayout(root, scratch) {
  const artifactRoot = path.join(scratch, 'artifacts');
  const imageParent = path.join(scratch, 'synthetic-images');
  const releaseParent = path.join(scratch, 'synthetic-releases');
  // 冻结 fixture 位于同次 scratch/source-parent 内；只禁止实际输入/输出根交叉。
  for (const folder of [artifactRoot, imageParent, releaseParent])
    if (folder === root || within(root, folder) || within(folder, root)) fail();
  return { artifactRoot, imageParent, releaseParent };
}

export async function assembleSyntheticCandidates(raw) {
  const input = validateSyntheticInput(raw);
  const scratch = input.scratchDirectory;
  await safePath(scratch, true);
  const { artifactRoot, imageParent, releaseParent } = syntheticLayout(ROOT, scratch);
  await safePath(artifactRoot, true);
  const identity = resolveCleanBuildIdentity(ROOT);
  const jar = await bytes(path.join(artifactRoot, 'api.jar'), 1024 ** 3);
  const extension = await bytes(path.join(artifactRoot, 'extension.zip'), 256 * 1024 ** 2);
  const compose = await bytes(path.join(ROOT, 'ops/docker/compose.yaml'), 1_000_000);
  const sql = await bytes(path.join(ROOT, 'infra/postgres/schema.sql'), 16 * 1024 ** 2);
  const datasetBytes = [];
  for (const row of input.datasets) {
    const data = await bytes(path.join(scratch, 'producer-output/synthetic-datasets', row.file), 64 * 1024 ** 2);
    if (data.length !== row.bytes || sha(data) !== row.sha256) fail();
    datasetBytes.push(data);
  }
  await mkdir(imageParent, { mode: 0o700 });
  await mkdir(releaseParent, { mode: 0o700 });
  const image = await buildCandidateImages({ repoRoot: ROOT, artifactRoot, outputParent: imageParent,
    endpoint: input.endpoint, platform: 'linux/arm64', descriptor: { schemaVersion: 1,
      softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit, jar: { path: 'api.jar', ...fact(jar) } } });
  const imageSha = sha(await bytes(path.join(image.candidateDirectory, 'candidate.json'), 2_000_000));
  const records = []; const licenses = [];
  for (const [id, component] of [['lexiflow', 'LexiFlow'], ['ext-third-party', 'extension-third-party'],
    ['api-runtime', 'API-runtime'], ['postgresql', 'PostgreSQL'], ['dataset-license', 'dataset']]) {
    const noticePath = `licenses/${id}.txt`;
    const notice = Buffer.from(`Internal synthetic runtime acceptance only; no redistribution authorization. Component: ${component}.\n`);
    await mkdir(path.join(artifactRoot, 'licenses'), { recursive: true, mode: 0o700 });
    await writeFile(path.join(artifactRoot, noticePath), notice, { flag: 'wx', mode: 0o600 });
    records.push({ role: 'license', path: noticePath, ...fact(notice), licenseIds: [id] });
    licenses.push({ id, component, licenseId: 'UNVERIFIED-SYNTHETIC-ONLY',
      licenseName: 'Internal synthetic test only; no redistribution authorization',
      sourceUrl: 'https://example.invalid/synthetic/runtime-check', noticePath,
      noticeBytes: notice.length, noticeSha256: sha(notice) });
  }
  records.push({ role: 'api-image', platform: 'linux/arm64', licenseIds: ['lexiflow', 'api-runtime'] },
    { role: 'postgres-image', platform: 'linux/arm64', licenseIds: ['postgresql'] });
  for (const [role, relative, data, ids, metadata] of [
    ['compose', 'compose.yaml', compose, ['lexiflow']], ['sql', 'schema.sql', sql, ['lexiflow']],
    ['extension', 'extension.zip', extension, ['lexiflow', 'ext-third-party'],
      { softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit }]]) {
    if (role !== 'extension') await writeFile(path.join(artifactRoot, relative), data, { flag: 'wx', mode: 0o600 });
    records.push({ role, path: relative, ...fact(data), licenseIds: ids, ...(metadata ? { metadata } : {}) });
  }
  await mkdir(path.join(artifactRoot, 'dataset'), { mode: 0o700 });
  const candidates = [];
  for (const [i, row] of input.datasets.entries()) {
    await writeFile(path.join(artifactRoot, 'dataset', row.file), datasetBytes[i], { flag: 'wx', mode: 0o600 });
    const assembled = await assembleReleaseCandidate({ repoRoot: ROOT, artifactRoot, outputParent: releaseParent,
      imageCandidates: [{ directory: image.candidateDirectory, sha256: imageSha }],
      descriptor: syntheticDescriptor(identity, records, licenses, row, sha(sql)) });
    const candidateSha256 = sha(await bytes(path.join(assembled.candidateDirectory, 'candidate.json'), 2_000_000));
    const verified = await verifyReleaseCandidate({ candidateDirectory: assembled.candidateDirectory, candidateSha256 });
    if (verified.candidateSha256 !== candidateSha256 || verified.buildIdentity.buildId !== identity.buildId
      || !within(releaseParent, assembled.candidateDirectory)) fail();
    candidates.push({ candidateDirectory: assembled.candidateDirectory, candidateSha256 });
  }
  return { candidates, datasets: input.datasets.map(row => ({ version: row.datasetVersion, sha256: row.sha256 })),
    softwareVersion: identity.softwareVersion, apiContract: 'caption-hints.v2' };
}

export async function readSyntheticInput(stream) {
  const chunks = []; let size = 0;
  for await (const chunk of stream) { size += chunk.length; if (size > 64 * 1024) fail(); chunks.push(chunk); }
  const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
  const parsed = JSON.parse(text);
  // 唯一 Python caller 发送规范 JSON；重复键、空白与转义的第二协议不接受。
  if (JSON.stringify(parsed) !== text) fail();
  return validateSyntheticInput(parsed);
}
let direct = false;
try { direct = realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch {}
if (direct) {
  try {
    if (process.argv.length !== 2) fail();
    process.stdout.write(`${JSON.stringify(await assembleSyntheticCandidates(await readSyntheticInput(process.stdin)))}\n`);
  } catch { process.stderr.write('SYNTHETIC_CANDIDATES_FAILED\n'); process.exitCode = 1; }
}
