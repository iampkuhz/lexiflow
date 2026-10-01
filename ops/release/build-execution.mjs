import { createHash } from 'node:crypto';
import { constants, createReadStream, createWriteStream } from 'node:fs';
import { chmod, lstat, link, mkdir, mkdtemp, readFile, rm, unlink, writeFile } from 'node:fs/promises';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import path from 'node:path';
import { prepareImageBuild } from './build.mjs';
import { runDocker } from './build-command.mjs';

const reject = (code) => { throw new Error(code); };
const exactKeys = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join('\0') === [...names].sort().join('\0');
const knownDockerErrors = new Set(['DOCKER_COMMAND_FAILED', 'DOCKER_COMMAND_TIMEOUT', 'DOCKER_OUTPUT_LIMIT', 'DOCKER_UNAVAILABLE']);
const MAX_IMAGE_BYTES = 2 ** 40;
const FILE_FLAGS = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0);
const within = (parent, child) => {
  const relative = path.relative(parent, child);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
};
const hash = () => createHash('sha256');

async function assertSafePath(target, mustExist = true, directory = false) {
  if (typeof target !== 'string' || !path.isAbsolute(target) || /[\u0000-\u001f\u007f]/.test(target)) reject('BUILD_INPUT_INVALID');
  let cursor = path.parse(target).root;
  for (const part of target.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const stat = await lstat(cursor).catch((error) => error.code === 'ENOENT' && !mustExist ? null : reject('BUILD_INPUT_INVALID'));
    if (stat?.isSymbolicLink()) reject('BUILD_INPUT_INVALID');
  }
  if (mustExist) {
    const stat = await lstat(target).catch(() => reject('BUILD_INPUT_INVALID'));
    if (directory ? !stat.isDirectory() : !stat.isFile()) reject('BUILD_INPUT_INVALID');
  }
}

function parseEndpoint(endpoint) {
  if (typeof endpoint !== 'string' || !endpoint.startsWith('unix://') || /[\u0000-\u001f\u007f]/.test(endpoint)) reject('ENDPOINT_INVALID');
  const socketPath = endpoint.slice('unix://'.length);
  if (!path.isAbsolute(socketPath) || socketPath.startsWith('//') || socketPath === '/') reject('ENDPOINT_INVALID');
  return endpoint;
}

async function copyVerified(sourceRoot, relative, destination, bytesExpected, digestExpected) {
  const source = path.join(sourceRoot, ...relative.split('/'));
  let cursor = sourceRoot; let stat;
  for (const part of relative.split('/')) {
    cursor = path.join(cursor, part);
    stat = await lstat(cursor).catch(() => null);
    if (!stat || stat.isSymbolicLink()) reject('BUILD_INPUT_REJECTED');
  }
  if (!stat.isFile() || stat.size !== bytesExpected) reject('BUILD_INPUT_REJECTED');
  const checksum = hash(); let bytes = 0;
  const meter = new Transform({ transform(chunk, _encoding, callback) {
    bytes += chunk.length;
    if (bytes > bytesExpected) { callback(new Error('BUILD_INPUT_REJECTED')); return; }
    checksum.update(chunk); callback(null, chunk);
  } });
  try {
    await pipeline(createReadStream(source, { flags: FILE_FLAGS }), meter, createWriteStream(destination, { flags: 'wx', mode: 0o600 }));
  } catch (error) {
    if (error.message === 'BUILD_INPUT_REJECTED') reject(error.message);
    reject('BUILD_CONTEXT_COPY_FAILED');
  }
  if (bytes !== bytesExpected || checksum.digest('hex') !== digestExpected) reject('BUILD_INPUT_REJECTED');
}

async function streamDigest(file, maximum = MAX_IMAGE_BYTES) {
  await assertSafePath(file, true, false).catch(() => reject('IMAGE_ARCHIVE_INVALID'));
  const stat = await lstat(file).catch(() => null);
  if (!stat?.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > maximum) reject('IMAGE_ARCHIVE_INVALID');
  const checksum = hash(); let bytes = 0;
  try {
    for await (const chunk of createReadStream(file, { flags: FILE_FLAGS })) {
      bytes += chunk.length;
      if (bytes > maximum) reject('IMAGE_ARCHIVE_INVALID');
      checksum.update(chunk);
    }
  } catch (error) {
    if (error.message === 'IMAGE_ARCHIVE_INVALID') throw error;
    reject('IMAGE_ARCHIVE_INVALID');
  }
  if (bytes === 0) reject('IMAGE_ARCHIVE_INVALID');
  return { bytes, sha256: checksum.digest('hex') };
}

function parseJson(text, code) {
  try { return JSON.parse(text); } catch { reject(code); }
}

async function inspectJson(args, context, expectedPlatform, expectedId) {
  const value = parseJson(await runDocker(args, context), 'DOCKER_METADATA_INVALID');
  if (!Array.isArray(value) || value.length !== 1 || !value[0] || typeof value[0] !== 'object' || Array.isArray(value[0])) reject('DOCKER_METADATA_INVALID');
  const image = value[0];
  const aliases = expectedPlatform === 'linux/amd64' ? ['amd64', 'x86_64'] : ['arm64', 'aarch64'];
  if (image.Id !== expectedId || image.Os !== 'linux' || !aliases.includes(image.Architecture)) reject('BASE_IMAGE_MISMATCH');
  return image;
}

async function prepareContext(plan, repoRoot, artifactRoot, candidateDirectory) {
  const contexts = new Map();
  await mkdir(path.join(candidateDirectory, 'work'), { mode: 0o700 });
  for (const item of plan.platforms) {
    const contextRoot = path.join(candidateDirectory, 'work', item.platform.split('/')[1]);
    await mkdir(contextRoot, { recursive: false, mode: 0o700 });
    for (const file of item.contextFiles) {
      const target = path.join(contextRoot, file.target);
      const sourceRoot = file.target === 'lexiflow-api.jar' ? artifactRoot : repoRoot;
      const record = file.target === 'lexiflow-api.jar' ? plan.jar : plan.templates.find((template) => template.path === file.path);
      if (!record) reject('BUILD_CONTEXT_INVALID');
      await copyVerified(sourceRoot, file.path, target, record.bytes, record.sha256);
    }
    contexts.set(item.platform, contextRoot);
  }
  return contexts;
}

async function readImageId(iidFile) {
  const stat = await lstat(iidFile).catch(() => null);
  if (!stat?.isFile() || stat.isSymbolicLink() || stat.size > 72) reject('IMAGE_ID_INVALID');
  let text;
  try { text = await readFile(iidFile, 'utf8'); } catch { reject('IMAGE_ID_INVALID'); }
  const value = text.endsWith('\n') ? text.slice(0, -1) : text;
  if (!/^sha256:[a-f0-9]{64}$/.test(value) || text !== value && text !== `${value}\n`) reject('IMAGE_ID_INVALID');
  return value;
}

async function buildOnePlatform(planItem, contextRoot, candidateDirectory, dockerContext) {
  const resultIds = [];
  for (const buildArgv of [planItem.commands[2], planItem.commands[3]]) {
    const iidFlag = buildArgv.indexOf('--iidfile');
    if (buildArgv[0] !== 'docker' || buildArgv[1] !== 'build' || iidFlag < 0 || !buildArgv[iidFlag + 1]
      || path.basename(buildArgv[iidFlag + 1]) !== buildArgv[iidFlag + 1]) reject('BUILD_PLAN_INVALID');
    const iidFile = path.join(contextRoot, buildArgv[iidFlag + 1]);
    await runDocker(buildArgv.slice(1), { ...dockerContext, cwd: contextRoot, timeoutMs: 900_000 });
    const imageId = await readImageId(iidFile);
    const architecture = planItem.platform;
    await inspectJson(['image', 'inspect', imageId], dockerContext, architecture, imageId);
    await rm(iidFile, { force: false });
    resultIds.push(imageId);
  }
  const records = [];
  for (const [index, role] of ['api-image', 'postgres-image'].entries()) {
    const imageId = resultIds[index];
    const relative = `images/${planItem.platform.replace('/', '-')}/${index === 0 ? 'api-image' : 'postgres-image'}.tar`;
    const destination = path.join(candidateDirectory, ...relative.split('/'));
    await mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
    await runDocker(['image', 'save', '--output', destination, imageId], { ...dockerContext, cwd: candidateDirectory, timeoutMs: 300_000 });
    const summary = await streamDigest(destination);
    records.push({ role, platform: planItem.platform, path: relative, bytes: summary.bytes, sha256: summary.sha256, imageDigest: imageId });
  }
  return records;
}

export async function buildCandidateImages(input) {
  if (!exactKeys(input, ['repoRoot', 'artifactRoot', 'descriptor', 'outputParent', 'endpoint', 'platform'])) reject('BUILD_INPUT_INVALID');
  const { repoRoot, artifactRoot, descriptor, outputParent, platform: selectedPlatform } = input;
  if (!['linux/amd64', 'linux/arm64'].includes(selectedPlatform)) reject('BUILD_INPUT_INVALID');
  if (typeof outputParent !== 'string' || !path.isAbsolute(outputParent) || /[\u0000-\u001f\u007f]/.test(outputParent)) reject('OUTPUT_PARENT_INVALID');
  const endpoint = parseEndpoint(input.endpoint);
  let plan;
  try { plan = await prepareImageBuild({ repoRoot, artifactRoot, descriptor }); }
  catch { reject('BUILD_INPUT_REJECTED'); }
  const buildInputSha256 = hash().update(JSON.stringify(plan), 'utf8').digest('hex');
  const selectedPlan = plan.platforms.find((item) => item.platform === selectedPlatform);
  if (!selectedPlan) reject('BUILD_INPUT_REJECTED');
  const selectedBase = { platform: selectedPlan.platform, javaRuntime: { ...selectedPlan.javaRuntime }, postgresRuntime: { ...selectedPlan.postgresRuntime } };
  const repo = path.resolve(repoRoot); const artifacts = path.resolve(artifactRoot); const parent = path.resolve(outputParent);
  await assertSafePath(outputParent, true, true);
  if (within(repo, parent) || within(parent, repo) || within(artifacts, parent) || within(parent, artifacts)) reject('OUTPUT_PARENT_INVALID');
  const candidateDirectory = await mkdtemp(path.join(parent, '.lexiflow-image-candidate-'))
    .catch(() => reject('BUILD_CANDIDATE_FAILED'));
  let retained = false;
  try {
    await chmod(candidateDirectory, 0o700);
    const configDirectory = path.join(candidateDirectory, 'config');
    await mkdir(configDirectory, { mode: 0o700 });
    await writeFile(path.join(configDirectory, 'config.json'), '{"auths":{}}\n', { flag: 'wx', mode: 0o600 });
    const dockerContext = { cwd: candidateDirectory, configDirectory, endpoint, timeoutMs: 30_000 };
    const version = parseJson(await runDocker(['version', '--format', '{{json .}}'], dockerContext), 'DOCKER_METADATA_INVALID');
    if (!version || typeof version !== 'object' || Array.isArray(version) || !version.Server) reject('DOCKER_UNAVAILABLE');
    const info = parseJson(await runDocker(['info', '--format', '{{json .}}'], dockerContext), 'DOCKER_METADATA_INVALID');
    const expectedArch = selectedPlatform === 'linux/amd64' ? ['amd64', 'x86_64'] : ['arm64', 'aarch64'];
    if (!info || typeof info !== 'object' || Array.isArray(info) || info.OSType !== 'linux'
      || !expectedArch.includes(info.Architecture)) reject('DOCKER_PLATFORM_MISMATCH');
    await inspectJson(['image', 'inspect', selectedPlan.javaRuntime.reference], dockerContext, selectedPlatform, selectedPlan.javaRuntime.imageId);
    await inspectJson(['image', 'inspect', selectedPlan.postgresRuntime.reference], dockerContext, selectedPlatform, selectedPlan.postgresRuntime.imageId);
    const contexts = await prepareContext({ ...plan, platforms: [selectedPlan] }, repo, artifacts, candidateDirectory);
    const records = await buildOnePlatform(selectedPlan, contexts.get(selectedPlatform), candidateDirectory, dockerContext);
    let currentPlan;
    try { currentPlan = await prepareImageBuild({ repoRoot, artifactRoot, descriptor }); }
    catch { reject('BUILD_INPUT_CHANGED'); }
    if (JSON.stringify(currentPlan) !== JSON.stringify(plan)) reject('BUILD_INPUT_CHANGED');
    const currentInfo = parseJson(await runDocker(['info', '--format', '{{json .}}'], dockerContext), 'DOCKER_METADATA_INVALID');
    if (currentInfo?.OSType !== 'linux' || !expectedArch.includes(currentInfo.Architecture)) reject('DOCKER_PLATFORM_MISMATCH');
    // 后续构建或导出不能悄悄改变先前已计入候选记录的归档。
    for (const record of records) {
      const actual = await streamDigest(path.join(candidateDirectory, record.path));
      if (actual.bytes !== record.bytes || actual.sha256 !== record.sha256) reject('IMAGE_ARCHIVE_CHANGED');
    }
    await rm(path.join(candidateDirectory, 'work'), { recursive: true, force: false });
    await rm(configDirectory, { recursive: true, force: false });
    records.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
    const candidate = {
      schemaVersion: 1,
      kind: 'lexiflow-image-candidate',
      softwareVersion: plan.softwareVersion,
      sourceCommit: plan.sourceCommit,
      platform: selectedPlatform,
      buildInputSha256,
      baseImages: [selectedBase],
      artifacts: records,
    };
    const candidateText = `${JSON.stringify(candidate, null, 2)}\n`;
    const temporaryMarker = path.join(candidateDirectory, '.candidate-pending.json');
    await writeFile(temporaryMarker, candidateText, { flag: 'wx', mode: 0o600 });
    await link(temporaryMarker, path.join(candidateDirectory, 'candidate.json'));
    await unlink(temporaryMarker);
    retained = true;
    return { candidateDirectory, candidate };
  } catch (error) {
    if (knownDockerErrors.has(error.message)) throw error;
    if (['BUILD_INPUT_INVALID', 'BUILD_INPUT_REJECTED', 'BUILD_INPUT_CHANGED', 'BUILD_CONTEXT_COPY_FAILED', 'BUILD_CONTEXT_INVALID', 'BUILD_PLAN_INVALID', 'DOCKER_METADATA_INVALID', 'DOCKER_PLATFORM_UNSUPPORTED', 'DOCKER_PLATFORM_MISMATCH', 'BASE_IMAGE_MISMATCH', 'IMAGE_ID_INVALID', 'IMAGE_ARCHIVE_INVALID', 'IMAGE_ARCHIVE_CHANGED', 'ENDPOINT_INVALID', 'OUTPUT_PARENT_INVALID', 'BUILD_CANDIDATE_FAILED'].includes(error.message)) throw error;
    reject('BUILD_CANDIDATE_FAILED');
  } finally {
    if (!retained) await rm(candidateDirectory, { recursive: true, force: true }).catch(() => {});
  }
}
