import { createHash, randomUUID } from 'node:crypto';
import { createReadStream, constants, existsSync, readdirSync, readFileSync } from 'node:fs';
import os from 'node:os';
import { copyFile, lstat, mkdir, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { apiContract } from '../../../scripts/contracts/contract-lock.mjs';
import { assertBuildIdentityMatches, resolveBuildIdentity } from '../../release/integrity/source-build-identity.mjs';
import { verifyJarIdentity } from '../../release/integrity/artifact-build-identity.mjs';
import { readExtensionFiles, readZipEntries } from '../../release/integrity/extension-archive-integrity.mjs';
import { readStableRegularFile, validateExtensionArchiveEntries } from '../../../extension/scripts/packaging/extension-archive-contract.mjs';
import { validateExpectedDataset } from '../../../extension/scripts/build/expected-dataset.mjs';
import { LOCAL_TEST_PORT } from '../loopback-ports.mjs';

export const names = Object.freeze({ zip: 'extension.zip', image: 'runtime-image.tar', record: 'candidate.json',
  plan: 'plan.json', chrome: 'chrome', dataset: 'dataset.zip', binding: 'expected-dataset.json' });
const hex = /^[a-f0-9]{64}$/u;
const imageId = /^sha256:[a-f0-9]{64}$/u;
const runtimeTag = /^localhost\/lexiflow-test:[a-f0-9]{64}-[a-f0-9]{12}$/u;
const fail = code => { throw new Error(code); };
const json = file => JSON.parse(readStableRegularFile(file, 64 * 1024).toString('utf8'));
const pretty = value => JSON.stringify(value, null, 2) + '\n';
const valid = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join(' ') === [...keys].sort().join(' ');
const env = () => ({ ...process.env, LANG: 'C', LC_ALL: 'C' });

export function call(root, file, args, options = {}) {
  const result = spawnSync(file, args, { cwd: root, env: options.env ?? env(), encoding: 'utf8',
    stdio: ['ignore', 'pipe', options.progress ? 'inherit' : 'pipe'], timeout: options.timeout ?? 120000,
    maxBuffer: options.maxBuffer ?? 512 * 1024 });
  if (result.status !== 0 || result.error || result.signal) {
    if (result.stderr) process.stderr.write(result.stderr);
    fail(options.failure ?? 'LOCAL_TEST_COMMAND_FAILED: ' + path.basename(file));
  }
  return result.stdout.trim();
}

async function directory(file, create = false) {
  if (create) await mkdir(file, { mode: 0o700 }).catch(error => { if (error.code !== 'EEXIST') throw error; });
  const info = await lstat(file);
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & 0o022))
    fail('LOCAL_TEST_DIRECTORY_UNSAFE');
}

export async function slotPaths(slot, create = false) {
  await directory(path.dirname(slot), create);
  await directory(slot, create);
  return { slot, current: path.join(slot, 'current'), input: path.join(slot, 'dataset') };
}

export async function shaFile(file, limit = 8 * 1024 ** 3) {
  const stat = await lstat(file);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > limit) fail('LOCAL_TEST_FILE_INVALID');
  const h = createHash('sha256');
  let bytes = 0;
  for await (const chunk of createReadStream(file, { flags: constants.O_RDONLY | constants.O_NOFOLLOW })) {
    bytes += chunk.length;
    if (bytes > stat.size) fail('LOCAL_TEST_FILE_CHANGED');
    h.update(chunk);
  }
  const after = await lstat(file);
  if (bytes !== stat.size || stat.ino !== after.ino || stat.dev !== after.dev || stat.ctimeMs !== after.ctimeMs)
    fail('LOCAL_TEST_FILE_CHANGED');
  return h.digest('hex');
}

function candidateRecord(dir) {
  const value = json(path.join(dir, names.record));
  if (!valid(value, ['role', 'imageId', 'tag', 'imageSha256', 'extensionSha256', 'apiContract']) ||
      value.role !== 'lexiflow-local-test' || !imageId.test(value.imageId) || !runtimeTag.test(value.tag) ||
      !hex.test(value.imageSha256) || !hex.test(value.extensionSha256) ||
      !/^caption-hints\.v[1-9][0-9]{0,8}$/u.test(value.apiContract)) fail('LOCAL_TEST_CANDIDATE_INVALID');
  return value;
}

export async function readCandidate(root, current) {
  await directory(current);
  const record = candidateRecord(current);
  const archive = path.join(current, names.zip), imageArchive = path.join(current, names.image);
  if (record.apiContract !== apiContract() || await shaFile(imageArchive) !== record.imageSha256)
    fail('LOCAL_TEST_CANDIDATE_CHANGED');
  const entries = await readExtensionFiles(archive, record.extensionSha256);
  const { identity, dataset } = validateExtensionArchiveEntries(entries, { apiPort: LOCAL_TEST_PORT });
  if (record.tag !== tagFor(identity, dataset)) fail('LOCAL_TEST_IMAGE_TAG_INVALID');
  const tarManifest = JSON.parse(call(root, 'tar', ['-xOf', imageArchive, 'manifest.json']));
  if (!Array.isArray(tarManifest) || tarManifest.length !== 1 || !Array.isArray(tarManifest[0]?.RepoTags) ||
      tarManifest[0].RepoTags.length !== 1 || tarManifest[0].RepoTags[0] !== record.tag ||
      !/^(?:blobs\/sha256\/[a-f0-9]{64}|[a-f0-9]{64}\.json)$/u.test(tarManifest[0]?.Config))
    fail('LOCAL_TEST_IMAGE_ARCHIVE_INVALID');
  // Docker archives name their config by its content ID; verify bytes without trimming.
  const raw = spawnSync('tar', ['-xOf', imageArchive, tarManifest[0].Config], { timeout: 120000, maxBuffer: 512 * 1024 });
  if (raw.status !== 0 || raw.error || 'sha256:' + createHash('sha256').update(raw.stdout).digest('hex') !== record.imageId)
    fail('LOCAL_TEST_IMAGE_ARCHIVE_ID_MISMATCH');
  const imageConfig = JSON.parse(raw.stdout.toString('utf8'));
  if (imageConfig.os !== 'linux' || imageConfig.architecture !== 'arm64') fail('LOCAL_TEST_IMAGE_PLATFORM_MISMATCH');
  return { ...record, archive, imageArchive, identity, dataset };
}

export function writePlan(current, plan) {
  return atomicJson(path.join(current, names.plan), plan);
}

export function loadPlan(current, candidate) {
  let plan;
  try { plan = json(path.join(current, names.plan)); }
  catch (error) {
    if (error.code === 'ENOENT') fail('LOCAL_TEST_PLAN_REQUIRED: 先执行 plan');
    throw error;
  }
  if (plan.status !== 'PASS' || plan.action !== 'plan' || !hex.test(plan.planSha256) ||
      plan.nextId !== candidate.imageId.slice(7) || plan.extensionSha256 !== candidate.extensionSha256)
    fail('LOCAL_TEST_PLAN_NOT_CURRENT');
  return plan;
}

async function atomicJson(file, value) {
  const temporary = file + '.' + randomUUID() + '.pending';
  try { await writeFile(temporary, pretty(value), { mode: 0o600, flag: 'wx' }); await rename(temporary, file); }
  finally { await rm(temporary, { force: true }); }
}

const tagFor = (identity, dataset) => 'localhost/lexiflow-test:' + identity.buildId + '-' + dataset.sha256.slice(0, 12);

export async function publishBuild(current, stage, publish) {
  // stage is exclusively owned by this build; current is never a symlink and never a partial tree.
  const previous = path.join(stage, 'previous');
  const old = await lstat(current).catch(error => { if (error.code !== 'ENOENT') throw error; return null; });
  if (old) { await directory(current); candidateRecord(current); await rename(current, previous); }
  try { await rename(publish, current); }
  catch (error) { if (old) await rename(previous, current); throw error; }
}

function javaEnv(root) {
  const inherited = env();
  if (inherited.LEXIFLOW_JAVA_HOME || existsSync(path.join(root, '.local/toolchains/jdk-25'))) return inherited;
  const valid = home => {
    try {
      const metadata = readFileSync(path.join(home, 'release'), 'utf8');
      return /^IMPLEMENTOR_VERSION="Temurin-25\./mu.test(metadata) && existsSync(path.join(home, 'bin/java'));
    } catch { return false; }
  };
  if (inherited.JAVA_HOME && valid(inherited.JAVA_HOME)) return inherited;
  const base = inherited.SDKMAN_CANDIDATES_DIR || path.join(inherited.SDKMAN_DIR || path.join(os.userInfo().homedir, '.sdkman'), 'candidates');
  const candidates = path.join(base, 'java');
  let names;
  try { names = readdirSync(candidates).filter(name => /^25\.[0-9.+]+-tem$/u.test(name)); }
  catch (error) { if (error.code !== 'ENOENT') throw error; return inherited; }
  const home = names.sort((a,b) => b.localeCompare(a, 'en', { numeric: true })).map(name => path.join(candidates, name)).find(valid);
  return home ? { ...inherited, LEXIFLOW_JAVA_HOME: home } : inherited;
}

export async function buildLocalTest(root, paths) {
  try { await directory(paths.input); }
  catch (error) {
    if (error.code === 'ENOENT') fail('LOCAL_TEST_DATASET_REQUIRED: ' + paths.input);
    throw error;
  }
  const input = path.join(paths.input, names.dataset);
  const binding = validateExpectedDataset(json(path.join(paths.input, names.binding)));
  const expected = binding.sha256;
  if (await shaFile(input) !== expected) fail('LOCAL_TEST_DATASET_DIGEST_MISMATCH');
  const identity = resolveBuildIdentity(root);
  const stage = await mkdtemp(path.join(paths.slot, '.build-'));
  const context = path.join(stage, 'context'), output = path.join(stage, 'next');
  const toolchain = javaEnv(root);
  const java = (args, progress = false) => call(root, 'python3', ['-m', 'scripts.local.run_with_java_toolchain', ...args],
    { timeout: 900000, progress, env: toolchain });
  const builder = (args, progress = false) => call(root, 'podman', ['--connection', 'podman-machine-default', ...args],
    { timeout: 900000, progress });
  let tag, leasedImage;
  const owner = 'lexiflow-local-test-build-' + randomUUID();
  let keepStage = false, published = false;
  try {
    await mkdir(context, { mode: 0o700 }); await mkdir(output, { mode: 0o700 });
    const dataset = path.join(context, names.dataset);
    await copyFile(input, dataset, constants.COPYFILE_EXCL);
    if (await shaFile(dataset) !== expected) fail('LOCAL_TEST_DATASET_DIGEST_MISMATCH');
    process.stderr.write('[LexiFlow] 构建测试后端并校验词库…\n');
    java(['backend/gradlew', '-p', 'backend', '--no-daemon', ':api:bootJar'], true);
    const jar = path.join(context, 'lexiflow-api.jar');
    await copyFile(path.join(root, 'backend/product/api/build/internal/boot/api-internal.jar'), jar);
    await verifyJarIdentity(jar, identity);
    if (java(['java', '-jar', jar, '--api-contract']) !== apiContract()) fail('LOCAL_TEST_JAR_CONTRACT_MISMATCH');
    java(['java', '-jar', jar, '--storage-contract']);
    const verified = JSON.parse(java(['java', '-jar', jar, '--release-dataset', 'verify', '--package', dataset, '--expected-sha256', expected]));
    if (verified.result !== 'PASS') fail('LOCAL_TEST_DATASET_NOT_VERIFIED');
    const manifest = JSON.parse((await readZipEntries(dataset, ['manifest.json'])).get('manifest.json').toString('utf8'));
    if (binding.preparationId !== manifest.preparationPolicy || binding.ruleId !== manifest.generationVersion ||
        binding.sqlVersion !== manifest.schemaSha256) fail('LOCAL_TEST_DATASET_IDENTITY_MISMATCH');
    const expectedFile = path.join(stage, 'expected-dataset.json');
    await writeFile(expectedFile, pretty(binding), { flag: 'wx', mode: 0o600 });
    process.stderr.write('[LexiFlow] 从源码打包 Chrome 测试版…\n');
    if (!await lstat(path.join(root, 'extension/node_modules/esbuild')).catch(error => {
      if (error.code !== 'ENOENT') throw error;
      return null;
    })) call(root, 'npm', ['--prefix', 'extension', 'ci', '--no-audit', '--no-fund'], { timeout: 180000, progress: true });
    const descriptor = JSON.parse(call(root, process.execPath, ['extension/scripts/packaging/package-extension-release.mjs',
      '--local-test', '--expected-dataset', expectedFile], { timeout: 180000, progress: true }));
    assertBuildIdentityMatches(identity, descriptor.buildIdentity);
    const entries = await readExtensionFiles(descriptor.archive, descriptor.sha256);
    validateExtensionArchiveEntries(entries, { apiPort: LOCAL_TEST_PORT, expectedDataset: binding });
    await copyFile(descriptor.archive, path.join(output, names.zip), constants.COPYFILE_EXCL);
    for (const [relative, contents] of entries) {
      // The archive verifier provided the exact project allowlist; never run a general-purpose extraction.
      const destination = path.join(output, names.chrome, relative);
      await mkdir(path.dirname(destination), { recursive: true, mode: 0o700 });
      await writeFile(destination, contents, { flag: 'wx', mode: 0o600 });
    }

    process.stderr.write('[LexiFlow] 准备固定测试槽镜像；不启动日常服务…\n');
    const engine = builder(['info', '--format', '{{.Host.Security.Rootless}} {{.Store.GraphRoot}}']);
    if (engine !== 'true /var/home/core/.local/share/containers/storage') fail('LOCAL_TEST_BUILD_ENGINE_UNAVAILABLE');
    const base = json(path.join(root, 'ops/docker/base-images.json')).baseImages.find(item => item.platform === 'linux/arm64')?.javaRuntime;
    if (!imageId.test(base?.imageId) || !/^docker\.io\/library\/eclipse-temurin@sha256:[a-f0-9]{64}$/u.test(base?.reference))
      fail('LOCAL_TEST_RUNTIME_BASE_INVALID');
    const inspect = target => JSON.parse(builder(['image', 'inspect', target]))[0];
    let baseInfo;
    try { baseInfo = inspect(base.imageId); } catch {
      builder(['pull', '--platform', 'linux/arm64', base.reference], true);
      baseInfo = inspect(base.imageId);
    }
    if (baseInfo.Id.replace(/^sha256:/u, '') !== base.imageId.slice(7) || baseInfo.Os !== 'linux' || baseInfo.Architecture !== 'arm64')
      fail('LOCAL_TEST_RUNTIME_BASE_MISMATCH');
    for (const file of ['Dockerfile.runtime', 'runtime-entrypoint.sh'])
      await copyFile(path.join(root, 'ops/docker', file), path.join(context, file));
    await mkdir(path.join(context, 'notices'), { mode: 0o700 });
    await copyFile(path.join(root, 'LICENSE'), path.join(context, 'notices/LICENSE'));
    await copyFile(path.join(root, 'backend/product/adapters/src/main/resources/lexicon/wordnet-3.0-license.txt'),
      path.join(context, 'notices/wordnet-3.0-license.txt'));
    const releaseIdentity = { schemaVersion: 1, softwareVersion: identity.softwareVersion, apiContract: apiContract() };
    await writeFile(path.join(context, 'release-identity.env'), Object.entries({
      ...releaseIdentity, sqlVersion: binding.sqlVersion, releaseId: binding.releaseId, preparationId: binding.preparationId,
      ruleId: binding.ruleId, datasetSha256: binding.sha256,
    }).map(([key, value]) => key + '=' + value).join('\n') + '\n', { flag: 'wx' });
    await writeFile(path.join(context, 'release-identity.json'), pretty({ ...releaseIdentity, buildIdentity: identity,
      sqlVersion: binding.sqlVersion, dataset: { releaseId: binding.releaseId, preparationId: binding.preparationId,
        ruleId: binding.ruleId, bytes: (await lstat(dataset)).size, sha256: binding.sha256 } }), { flag: 'wx' });

    const proposedTag = tagFor(identity, binding);
    const exists = spawnSync('podman', ['--connection', 'podman-machine-default', 'image', 'exists', proposedTag], { cwd: root, timeout: 30000 });
    if (exists.status !== 1) fail('LOCAL_TEST_BUILD_TAG_IN_USE');
    tag = proposedTag;
    const labels = {
      'io.lexiflow.local-slot': 'lexiflow-test', 'io.lexiflow.local-build-owner': owner,
      'io.lexiflow.build-id': identity.buildId, 'io.lexiflow.source-sha256': identity.sourceSha256,
      'org.opencontainers.image.revision': identity.sourceCommit, 'org.opencontainers.image.version': identity.softwareVersion,
      'io.lexiflow.dataset-sha256': binding.sha256,
    };
    builder(['build', '--format', 'docker', '--pull=never', '--network=none', '--platform', 'linux/arm64',
      '--build-arg', 'JAVA_RUNTIME_IMAGE=' + base.imageId.slice(7), ...Object.entries(labels).flatMap(([key, value]) => ['--label', key + '=' + value]),
      '-t', tag, '-f', path.join(context, 'Dockerfile.runtime'), context], true);
    const built = inspect(tag);
    if (built.Config?.Labels?.['io.lexiflow.local-build-owner'] !== owner || !/^(?:sha256:)?[a-f0-9]{64}$/u.test(built.Id))
      fail('LOCAL_TEST_BUILD_OWNERSHIP_UNPROVEN');
    leasedImage = 'sha256:' + built.Id.replace(/^sha256:/u, '');
    const imageArchive = path.join(output, names.image);
    builder(['save', '--format', 'docker-archive', '-o', imageArchive, tag]);
    await writeFile(path.join(output, names.record), pretty({ role: 'lexiflow-local-test', imageId: leasedImage, tag,
      apiContract: apiContract(), imageSha256: await shaFile(imageArchive), extensionSha256: descriptor.sha256 }), { flag: 'wx', mode: 0o600 });
    await readCandidate(root, output);
    assertBuildIdentityMatches(identity, resolveBuildIdentity(root));
    // Remove this build's image before advertising the bundle; the daily engine was not touched.
    builder(['image', 'rm', '--no-prune', tag]);
    leasedImage = undefined;
    await publishBuild(paths.current, stage, output);
    published = true;
    return { status: 'PASS', action: 'build', directory: paths.current, archive: path.join(paths.current, names.zip),
      chrome: path.join(paths.current, names.chrome) };
  } finally {
    try {
      if (tag) {
        const exists = spawnSync('podman', ['--connection', 'podman-machine-default', 'image', 'exists', tag], { cwd: root, timeout: 30000 });
        if (exists.status === 0) {
          const [row] = JSON.parse(builder(['image', 'inspect', tag]));
          if (row.Config?.Labels?.['io.lexiflow.local-build-owner'] !== owner ||
              (leasedImage && leasedImage.slice(7) !== row.Id.replace(/^sha256:/u,''))) fail('LOCAL_TEST_BUILD_CLEANUP_UNCONFIRMED');
          builder(['image', 'rm', '--no-prune', tag]);
        } else if (exists.status !== 1) fail('LOCAL_TEST_BUILD_CLEANUP_UNCONFIRMED');
      }
    } catch (error) { keepStage = true; throw error; }
    finally {
      // If both rename and rollback failed, keep the only copy of the old bundle.
      const previous = await lstat(path.join(stage, 'previous')).catch(error => {
        if (error.code !== 'ENOENT') throw error;
        return null;
      });
      if (!keepStage && (published || !previous)) await rm(stage, { recursive: true, force: true });
    }
  }
}
