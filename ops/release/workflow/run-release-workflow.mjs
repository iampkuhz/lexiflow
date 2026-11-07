#!/usr/bin/env node
import { createHash, randomUUID } from 'node:crypto';
import { constants, createReadStream, createWriteStream } from 'node:fs';
import { lstat, mkdir, mkdtemp, open, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { spawn, execFileSync } from 'node:child_process';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCandidateImages } from '../build/release-image-builder.mjs';
import { assembleReleaseCandidate } from '../build/release-candidate-assembler.mjs';
import { verifyReleaseCandidate } from '../integrity/release-candidate-verifier.mjs';
import { verifyJarIdentity } from '../integrity/artifact-build-identity.mjs';
import { checkReleaseTag, resolveBuildIdentity, assertBuildIdentityMatches } from '../integrity/source-build-identity.mjs';
import { loadBaseImageLock } from '../build/release-image-build-plan.mjs';
import { validateRelativePath } from '../integrity/release-artifact-manifest.mjs';
import { runRunnerCommand } from '../runner/runner.mjs';
import { runDocker } from '../build/release-build-process.mjs';
import { resolveHostBinding } from '../host_resources/release-host-identity.mjs';
import { resolveFormalInvocation, resolveReleaseExecutionEnvironment, releaseWorkflowDirectory, releaseGradleCacheEntries } from './release-execution-environment.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const HEX = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_JSON = 2_000_000;
const MAX_ARTIFACT = 2 ** 40;
const MAX_LICENSE_TABLE = 256 * 1024;
const MAX_LICENSES = 64;
const fail = code => { throw new Error(code); };
const exact = (v, keys) => v !== null && typeof v === 'object' && !Array.isArray(v)
  && Object.keys(v).sort().join('\0') === [...keys].sort().join('\0');
const validHostBinding = value => exact(value, ['scheme', 'sha256'])
  && value.scheme === 'lexiflow.host-binding.v1' && HEX.test(value.sha256);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const fixedReason = error => /^[A-Z][A-Z0-9_]+$/.test(error?.message || '') ? error.message : 'WORKFLOW_FAILED';
const stamp = value => [value.dev, value.ino, value.size, value.mode, value.mtimeMs, value.ctimeMs].join(':');
const within = (parent, child) => {
  const relative = path.relative(parent, child);
  return relative === '' || (relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
};

async function safePath(file, kind = 'file', missing = false) {
  if (typeof file !== 'string' || !path.isAbsolute(file) || path.normalize(file) !== file
    || /[\u0000-\u001f\u007f]/.test(file)) fail('PATH_INVALID');
  let cursor = path.parse(file).root;
  const parts = file.slice(cursor.length).split(path.sep).filter(Boolean);
  for (const [index, part] of parts.entries()) {
    cursor = path.join(cursor, part);
    const info = await lstat(cursor).catch(error => error.code === 'ENOENT' && missing ? null : fail('PATH_INVALID'));
    if (!info) continue;
    if (info.isSymbolicLink() || (index < parts.length - 1 && !info.isDirectory())) fail('PATH_INVALID');
    if (index === parts.length - 1 && (kind === 'file' ? !info.isFile()
      : kind === 'socket' ? !info.isSocket() : !info.isDirectory())) fail('PATH_INVALID');
  }
}

// JSON is a private control plane: reject duplicate keys and non-JSON numeric constants before parsing.
function parseStrictJson(bytes) {
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  let index = 0;
  const ws = () => { while (index < text.length && /[ \t\r\n]/.test(text[index])) index++; };
  const string = () => {
    const start = index++;
    while (index < text.length) {
      if (text[index] === '\\') { index += 2; continue; }
      if (text[index++] === '"') return JSON.parse(text.slice(start, index));
    }
    fail('JSON_INVALID');
  };
  const value = () => {
    ws();
    if (text[index] === '{') {
      index++; ws(); const out = Object.create(null); const seen = new Set();
      if (text[index] === '}') { index++; return out; }
      while (true) {
        ws(); if (text[index] !== '"') fail('JSON_INVALID');
        const key = string(); if (seen.has(key)) fail('JSON_DUPLICATE_KEY'); seen.add(key);
        ws(); if (text[index++] !== ':') fail('JSON_INVALID');
        out[key] = value(); ws();
        const delimiter = text[index++]; if (delimiter === '}') return out;
        if (delimiter !== ',') fail('JSON_INVALID');
      }
    }
    if (text[index] === '[') {
      index++; ws(); const out = [];
      if (text[index] === ']') { index++; return out; }
      while (true) {
        out.push(value()); ws(); const delimiter = text[index++];
        if (delimiter === ']') return out;
        if (delimiter !== ',') fail('JSON_INVALID');
      }
    }
    if (text[index] === '"') return string();
    const match = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(text.slice(index));
    if (!match) fail('JSON_INVALID');
    index += match[0].length;
    const parsed = JSON.parse(match[0]);
    if (typeof parsed === 'number' && !Number.isFinite(parsed)) fail('JSON_INVALID');
    return parsed;
  };
  const parsed = value(); ws(); if (index !== text.length) fail('JSON_INVALID');
  return parsed;
}

async function readBoundedJson(file, maximum = MAX_JSON) {
  await safePath(file);
  const fd = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await fd.stat();
    if (!before.isFile() || before.size < 1 || before.size > maximum) fail('JSON_INVALID');
    const buffer = Buffer.alloc(before.size + 1);
    let count = 0;
    while (count < buffer.length) {
      const next = await fd.read(buffer, count, buffer.length - count, null);
      if (!next.bytesRead) break;
      count += next.bytesRead;
    }
    const after = await fd.stat(); const named = await lstat(file);
    if (count !== before.size || count > maximum || stamp(after) !== stamp(before)
      || stamp(named) !== stamp(before) || named.isSymbolicLink()) fail('JSON_INVALID');
    return { value: parseStrictJson(buffer.subarray(0, count)), sha256: digest(buffer.subarray(0, count)) };
  } finally { await fd.close(); }
}

function requestShape(value) {
  if (!exact(value, ['releaseTag', 'dataset', 'noticesRoot', 'outputParent', 'endpoint'])
    || !exact(value.dataset, ['path', 'sha256', 'releaseId', 'preparationId', 'ruleId', 'sqlVersion'])
    || !/^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$/.test(value.releaseTag)
    || !HEX.test(value.dataset.sha256)
    || ![value.dataset.releaseId, value.dataset.preparationId, value.dataset.ruleId, value.dataset.sqlVersion].every(x => typeof x === 'string' && ID.test(x))
    || !/^unix:\/\/\/[A-Za-z0-9_./-]+$/.test(value.endpoint)) fail('REQUEST_INVALID');
  return value;
}

async function fileDigest(file, max = MAX_ARTIFACT) {
  await safePath(file);
  const fd = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await fd.stat();
    if (!before.isFile() || before.size < 1 || before.size > max) fail('ARTIFACT_INVALID');
    const hash = createHash('sha256'); const chunk = Buffer.alloc(65536); let bytes = 0;
    while (bytes < before.size + 1) {
      const next = await fd.read(chunk, 0, Math.min(chunk.length, before.size + 1 - bytes), null);
      if (!next.bytesRead) break;
      bytes += next.bytesRead; hash.update(chunk.subarray(0, next.bytesRead));
    }
    const after = await fd.stat(); const named = await lstat(file);
    if (bytes !== before.size || stamp(after) !== stamp(before)
      || stamp(named) !== stamp(before) || named.isSymbolicLink()) fail('ARTIFACT_CHANGED');
    return { bytes, sha256: hash.digest('hex') };
  } finally { await fd.close(); }
}

async function copyArtifact(source, target, expected) {
  await safePath(source); await safePath(path.dirname(target), 'directory');
  const actual = await fileDigest(source);
  if (actual.bytes !== expected.bytes || actual.sha256 !== expected.sha256) fail('ARTIFACT_CHANGED');
  const meter = createHash('sha256'); let bytes = 0;
  await pipeline(createReadStream(source, { flags: constants.O_RDONLY | constants.O_NOFOLLOW }),
    new Transform({ transform(chunk, _encoding, callback) {
      bytes += chunk.length; if (bytes > expected.bytes) return callback(new Error('ARTIFACT_CHANGED'));
      meter.update(chunk); callback(null, chunk);
    } }), createWriteStream(target, { flags: 'wx', mode: 0o600 }));
  const streamed = { bytes, sha256: meter.digest('hex') };
  if (streamed.bytes !== expected.bytes || streamed.sha256 !== expected.sha256) fail('ARTIFACT_CHANGED');
  const again = await fileDigest(source);
  if (again.sha256 !== streamed.sha256 || again.bytes !== streamed.bytes) fail('ARTIFACT_CHANGED');
  return streamed;
}

function killGroup(child, signal) {
  if (!child.pid) return;
  try { process.kill(-child.pid, signal); } catch { /* already gone */ }
}
function groupAlive(child) {
  if (!child.pid) return false;
  try { process.kill(-child.pid, 0); return true; }
  catch { return false; }
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function runCommand(command, args, { cwd, env, timeout = 1_200_000, logFile, abortSignal } = {}) {
  if (!logFile || !Number.isSafeInteger(timeout) || timeout < 1 || timeout > 5_400_000) fail('COMMAND_INPUT_INVALID');
  await safePath(path.dirname(logFile), 'directory');
  const log = await open(logFile, 'wx', 0o600);
  try {
    return await new Promise((resolve, reject) => {
      let child;
      try { child = spawn(command, args, { cwd, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] }); }
      catch { reject(new Error('CHILD_START_FAILED')); return; }
      let output = Buffer.alloc(0); let size = 0; let reason = null; let killTimer;
      let writes = Promise.resolve(); let logError = false;
      const stop = code => {
        if (reason) return;
        reason = code; killGroup(child, 'SIGTERM');
        killTimer = setTimeout(() => killGroup(child, 'SIGKILL'), 3000);
      };
      const timer = setTimeout(() => stop('CHILD_TIMEOUT'), timeout);
      const interrupt = () => stop('CHILD_CANCELLED');
      process.on('SIGINT', interrupt); process.on('SIGTERM', interrupt);
      abortSignal?.addEventListener('abort', interrupt, { once: true });
      if (abortSignal?.aborted) interrupt();
      for (const stream of [child.stdout, child.stderr]) stream.on('data', bytes => {
        size += bytes.length;
        if (size > 1_000_000) { stop('CHILD_OUTPUT_LIMIT'); return; }
        if (stream === child.stdout) output = Buffer.concat([output, bytes]);
        stream.pause();
        writes = writes.then(async () => {
          let offset = 0;
          while (offset < bytes.length) {
            const result = await log.write(bytes, offset, bytes.length - offset);
            if (!result.bytesWritten) throw new Error('LOG_WRITE_FAILED');
            offset += result.bytesWritten;
          }
        }).then(() => stream.resume()).catch(() => {
          logError = true; stop('CHILD_LOG_FAILED');
        });
      });
      child.on('error', () => stop('CHILD_START_FAILED'));
      child.on('close', code => {
        (async () => {
          clearTimeout(timer);
          if (groupAlive(child)) stop(reason || 'CHILD_GROUP_LEFT_RUNNING');
          for (let i = 0; i < 30 && groupAlive(child); i++) {
            if (i === 10) killGroup(child, 'SIGKILL');
            await pause(100);
          }
          if (groupAlive(child)) reason = 'CHILD_GROUP_UNSETTLED';
          clearTimeout(killTimer);
          await writes;
          await log.sync();
          process.off('SIGINT', interrupt); process.off('SIGTERM', interrupt);
          abortSignal?.removeEventListener('abort', interrupt);
          if (logError) reason = 'CHILD_LOG_FAILED';
          if (code === 0 && !reason) resolve(output.toString('utf8'));
          else { const error = new Error(reason || 'CHILD_FAILED'); error.publicOutput = output.toString('utf8'); reject(error); }
        })().catch(() => {
          process.off('SIGINT', interrupt); process.off('SIGTERM', interrupt);
          abortSignal?.removeEventListener('abort', interrupt);
          reject(new Error('CHILD_SETTLE_FAILED'));
        });
      });
    });
  } finally { await log.close(); }
}

const ledgerFields = ['id', 'component', 'licenseId', 'licenseName', 'sourceUrl', 'noticePath', 'noticeBytes', 'noticeSha256'];
async function loadLicenses(root, noticesRoot, trackedCheck) {
  await trackedCheck(root);
  const ledger = (await readBoundedJson(path.join(root, 'ops/release/distribution-licenses.json'), MAX_LICENSE_TABLE)).value;
  if (!exact(ledger, ['schema_version', 'licenses']) || ledger.schema_version !== 'lexiflow.distribution-licenses.v1'
    || !Array.isArray(ledger.licenses) || ledger.licenses.length < 4 || ledger.licenses.length > MAX_LICENSES) fail('LICENSE_LEDGER_INVALID');
  const ids = new Set(); const notices = new Map();
  let lastId = '';
  for (const item of ledger.licenses) {
    if (!exact(item, ledgerFields) || !ID.test(item.id) || ids.has(item.id) || item.id <= lastId
      || !['LexiFlow', 'API-runtime', 'extension-third-party', 'dataset'].includes(item.component)
      || ![item.licenseId, item.licenseName].every(s => typeof s === 'string' && s.length > 0 && !/UNVERIFIED/i.test(s))
      || !/^https:\/\//.test(item.sourceUrl) || !Number.isSafeInteger(item.noticeBytes)
      || item.noticeBytes < 1 || item.noticeBytes > 2 ** 40 || !HEX.test(item.noticeSha256)) fail('LICENSE_LEDGER_INVALID');
    validateRelativePath(item.noticePath);
    ids.add(item.id);
    lastId = item.id;
    const notice = notices.get(item.noticePath) ?? await fileDigest(path.join(noticesRoot, item.noticePath));
    if (notice.bytes !== item.noticeBytes || notice.sha256 !== item.noticeSha256) fail('NOTICE_MISMATCH');
    notices.set(item.noticePath, notice);
  }
  for (const component of ['LexiFlow', 'API-runtime', 'extension-third-party', 'dataset'])
    if (!ledger.licenses.some(item => item.component === component)) fail('LICENSE_LEDGER_INVALID');
  return { licenses: ledger.licenses, notices };
}

async function writePrivate(file, value) {
  const fd = await open(file, 'wx', 0o600);
  try { await fd.writeFile(JSON.stringify(value) + '\n'); await fd.sync(); }
  finally { await fd.close(); }
}
function licenseIds(licenses, ...components) {
  return licenses.filter(item => components.includes(item.component)).map(item => item.id);
}
async function addArtifact(records, role, summary, relative, licenses, components, metadata) {
  records.push({ role, path: relative, ...summary, licenseIds: licenseIds(licenses, ...components),
    ...(metadata ? { metadata } : {}) });
  return summary;
}

async function preflightDockerEngine({ endpoint, baseImages, quality, dockerCommand = runDocker }) {
  if (typeof endpoint !== 'string' || !endpoint.startsWith('unix://') || !Array.isArray(baseImages)
    || baseImages.length !== 1 || baseImages[0]?.platform !== 'linux/arm64') fail('DOCKER_PREFLIGHT_INPUT_INVALID');
  const bases = baseImages[0];
  for (const image of [bases.javaRuntime]) {
    if (!image || typeof image.reference !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(image.imageId)) fail('BASE_IMAGE_MISSING');
  }
  const parent = path.dirname(quality);
  await safePath(parent, 'directory', true);
  await mkdir(parent, { recursive: true, mode: 0o700 });
  await safePath(parent, 'directory');
  const configDirectory = await mkdtemp(path.join(parent, '.docker-preflight-'));
  try {
    await writePrivate(path.join(configDirectory, 'config.json'), { auths: {} });
    const context = { cwd: configDirectory, configDirectory, endpoint, timeoutMs: 30_000 };
    const version = parseStrictJson(Buffer.from((await dockerCommand(['version', '--format', '{{json .}}'], context)).trim()));
    if (!version?.Server) fail('DOCKER_UNAVAILABLE');
    const info = parseStrictJson(Buffer.from((await dockerCommand(['info', '--format', '{{json .}}'], context)).trim()));
    if (info?.OSType !== 'linux' || !['arm64', 'aarch64'].includes(info.Architecture)) fail('DOCKER_PLATFORM_MISMATCH');
    for (const image of [bases.javaRuntime]) {
      const inspected = parseStrictJson(Buffer.from((await dockerCommand(['image', 'inspect', image.reference], context)).trim()));
      if (!Array.isArray(inspected) || inspected.length !== 1 || inspected[0]?.Id !== image.imageId
        || inspected[0]?.Os !== 'linux' || !['arm64', 'aarch64'].includes(inspected[0]?.Architecture)) fail('BASE_IMAGE_MISMATCH');
    }
  } finally { await rm(configDirectory, { recursive: true, force: false }); }
}

export async function prepareWorkflow(requestFile, options = {}) {
  const root = options.root ?? ROOT;
  const buildImages = options.buildCandidateImages ?? buildCandidateImages;
  const assemble = options.assembleReleaseCandidate ?? assembleReleaseCandidate;
  const verify = options.verifyReleaseCandidate ?? verifyReleaseCandidate;
  const command = options.runCommand ?? runCommand;
  const tagCheck = options.tagChecker ?? checkReleaseTag;
  const baseLoad = options.baseImageLoader ?? loadBaseImageLock;
  const endpointCheck = options.endpointChecker ?? (async endpoint => {
    const socket = endpoint.slice('unix://'.length);
    await safePath(socket, 'socket');
  });
  const trackedCheck = options.ledgerTrackedCheck ?? (repo => {
    try { execFileSync('git', ['-C', repo, 'ls-files', '--error-unmatch', '--', 'ops/release/distribution-licenses.json'],
      { stdio: 'ignore', timeout: 10_000 }); }
    catch { fail('LICENSE_LEDGER_UNTRACKED'); }
  });
  // Private pure boundary seam for deterministic tests; the CLI has no host-source option.
  const hostBindingResolver = options.testOnlyHostBindingResolver ?? resolveHostBinding;
  let guard;
  const timings = []; const invocationStarted = performance.now();
  let failurePhase = 'input-preflight', inputPreflightRecorded = false;
  const timed = async (name, work) => {
    if (!inputPreflightRecorded) {
      timings.push({ phase: 'input-preflight', durationMs: Math.max(0, Math.round(performance.now() - invocationStarted)) });
      inputPreflightRecorded = true;
    }
    failurePhase = name;
    const started = performance.now();
    try { return await work(); }
    finally { timings.push({ phase: name, durationMs: Math.max(0, Math.round(performance.now() - started)) }); }
  };
  const result = (value, remoteWriteState = 'not-started') => {
    if (options.diagnostics !== true) return value;
    const elapsedMs = Math.max(0, Math.round(performance.now() - invocationStarted));
    if (!inputPreflightRecorded) timings.push({ phase: 'input-preflight', durationMs: elapsedMs });
    const measuredMs = timings.reduce((total, phase) => total + phase.durationMs, 0);
    return { ...value, diagnostics: { phases: timings, elapsedMs, overheadMs: Math.max(0, elapsedMs - measuredMs),
      failurePhase: value.status === 'PASS' ? null : failurePhase, remoteWriteState } };
  };
  try {
    const initialHostBinding = hostBindingResolver();
    if ((options.platform ?? process.platform) !== 'darwin' || (options.arch ?? process.arch) !== 'arm64') fail('HOST_UNSUPPORTED');
    const { value: raw, sha256: requestSha256 } = await readBoundedJson(requestFile);
    const request = requestShape(raw);
    await endpointCheck(request.endpoint);
    await safePath(root, 'directory');
    for (const file of [request.dataset.path, request.noticesRoot])
      await safePath(file, file === request.dataset.path ? 'file' : 'directory');
    await safePath(request.outputParent, 'directory');
    if (within(root, request.outputParent) || within(request.outputParent, root)
      || (await readdir(request.outputParent)).length !== 0) fail('OUTPUT_PARENT_INVALID');
    const identity = tagCheck(request.releaseTag, root);
    const quality = releaseWorkflowDirectory(root);
    await safePath(quality, 'directory', true);
    try {
      await lstat(quality);
      const existing = await readdir(quality);
      if (existing.length && (existing.length !== 1 || existing[0] !== 'prepare.lock')) fail('WORKFLOW_RESIDUE');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const runtimeConfig = path.join(root, '.local/lexiflow/runtime.json');
    try { await lstat(runtimeConfig); fail('PERSONAL_CONFIG_PRESENT'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const dataset = await fileDigest(request.dataset.path);
    if (dataset.sha256 !== request.dataset.sha256) fail('DATASET_DIGEST_MISMATCH');
    const expectedDataset = { releaseId: request.dataset.releaseId, preparationId: request.dataset.preparationId,
      ruleId: request.dataset.ruleId, sqlVersion: request.dataset.sqlVersion, sha256: dataset.sha256 };
    const { licenses, notices } = await timed('license-validation', () => loadLicenses(root, request.noticesRoot, trackedCheck));
    const baseImages = (await timed('base-image-lock', () => baseLoad(root))).filter(item => item.platform === 'linux/arm64');
    if (baseImages.length !== 1) fail('ARM64_BASE_IMAGE_MISSING');
    const environmentResolver = options.environmentResolver ?? resolveReleaseExecutionEnvironment;
    const env = await timed('environment-preflight', () => {
      const resolved = environmentResolver({ root, purpose: 'prepare' });
      if (!resolved || resolved.npm_config_cache !== resolved.LEXIFLOW_RELEASE_NPM_CACHE
        || resolved.LEXIFLOW_RELEASE_ARM64_DOCKER_HOST !== request.endpoint
        || resolved.npm_config_userconfig !== path.join(quality, 'npmrc-empty')
        || resolved.GRADLE_USER_HOME !== path.join(quality, 'gradle-home')) fail('RELEASE_ENVIRONMENT_INVALID');
      return resolved;
    });
    const gradleCacheEntries = releaseGradleCacheEntries(root, env.LEXIFLOW_RELEASE_GRADLE_CACHE);
    await timed('docker-engine-preflight', () => preflightDockerEngine({ endpoint: request.endpoint, baseImages, quality,
      dockerCommand: options.dockerCommand ?? runDocker }));

    // 所有未就绪输入和只读工具探测完成后才取得版本目录锁并创建构建现场。
    failurePhase = 'prebuild-setup';
    await mkdir(quality, { recursive: true, mode: 0o700 });
    guard = await open(path.join(quality, 'prepare.lock'), 'wx', 0o600);
    if ((await readdir(quality)).some(name => name !== 'prepare.lock')) fail('WORKFLOW_RESIDUE');
    const expectedDatasetPath = path.join(quality, 'expected-dataset.json');
    await writeFile(expectedDatasetPath, `${JSON.stringify(expectedDataset)}\n`, { flag: 'wx', mode: 0o600 });
    const npmrc = await open(env.npm_config_userconfig, 'wx', 0o600);
    try { await npmrc.writeFile(''); await npmrc.sync(); } finally { await npmrc.close(); }
    await mkdir(path.join(env.GRADLE_USER_HOME, 'caches'), { recursive: true, mode: 0o700 });
    await mkdir(path.join(env.GRADLE_USER_HOME, 'wrapper'), { recursive: true, mode: 0o700 });
    for (const relative of gradleCacheEntries)
      await symlink(path.join(env.LEXIFLOW_RELEASE_GRADLE_CACHE, relative), path.join(env.GRADLE_USER_HOME, relative));
    await safePath(env.GRADLE_USER_HOME, 'directory');
    const artifactRoot = path.join(request.outputParent, 'artifacts');
    const imageParent = path.join(request.outputParent, 'images');
    const releaseParent = path.join(request.outputParent, 'releases');
    for (const folder of [artifactRoot, imageParent, releaseParent]) await mkdir(folder, { mode: 0o700 });
    await timed('compile', () => command('python3', ['-m', 'scripts.local.run_with_java_toolchain', 'backend/gradlew', '-p', 'backend', '--no-daemon', ':api:bootJar'],
      { cwd: root, env, timeout: 2_400_000, logFile: path.join(quality, 'gradle.log') }));
    await timed('npm-prepare', () => command('npm', ['ci'], { cwd: path.join(root, 'extension'), env, timeout: 900_000,
      logFile: path.join(quality, 'npm-ci.log') }));
    const extensionOutput = await timed('extension-package', () => command('node', ['extension/scripts/packaging/package-extension-release.mjs', '--expected-dataset', expectedDatasetPath], { cwd: root, env, timeout: 900_000,
      logFile: path.join(quality, 'extension-release.log') }));
    let extension; try { extension = parseStrictJson(Buffer.from(extensionOutput.trim(), 'utf8')); }
    catch { fail('EXTENSION_OUTPUT_INVALID'); }
    if (!exact(extension, ['buildIdentity', 'softwareVersion', 'sourceCommit', 'filename', 'bytes', 'sha256'])
      || extension.softwareVersion !== identity.softwareVersion || extension.sourceCommit !== identity.sourceCommit
      || extension.filename !== `lexiflow-extension-${identity.softwareVersion}.zip`) fail('EXTENSION_OUTPUT_INVALID');
    assertBuildIdentityMatches(extension.buildIdentity, identity);
    const jarSource = path.join(root, 'backend/product/api/build/internal/boot/api-internal.jar');
    const zipSource = path.join(root, 'tmp/releases', identity.softwareVersion, identity.sourceCommit, extension.filename);
    const jar = await fileDigest(jarSource);
    const zip = await fileDigest(zipSource);
    await verifyJarIdentity(jarSource, identity);
    if (zip.bytes !== extension.bytes || zip.sha256 !== extension.sha256) fail('EXTENSION_DIGEST_MISMATCH');
    await timed('dataset-verify', () => command('python3', ['-m', 'scripts.local.run_with_java_toolchain', 'java', '-jar', jarSource,
      '--release-dataset', 'verify', '--package', request.dataset.path, '--expected-sha256', request.dataset.sha256],
    { cwd: root, env, timeout: 900_000, logFile: path.join(quality, 'dataset-verify.log') }));
    failurePhase = 'artifact-staging';
    const stagedJar = path.join(artifactRoot, 'api.jar');
    await copyArtifact(jarSource, stagedJar, jar);
    const records = [];
    const datasetCopy = await copyArtifact(request.dataset.path, path.join(artifactRoot, 'dataset.zip'), dataset);
    await addArtifact(records, 'dataset', datasetCopy, 'dataset.zip', licenses,
      ['dataset'], { releaseId: request.dataset.releaseId, preparationId: request.dataset.preparationId,
        ruleId: request.dataset.ruleId, sqlVersion: request.dataset.sqlVersion });
    for (const [relative, summary] of notices) {
      const target = relative;
      await mkdir(path.dirname(path.join(artifactRoot, target)), { recursive: true, mode: 0o700 });
      const copiedNotice = await copyArtifact(path.join(request.noticesRoot, relative), path.join(artifactRoot, target), summary);
      const associated = licenses.filter(item => item.noticePath === relative).map(item => item.id);
      records.push({ role: 'license', path: target, ...copiedNotice, licenseIds: associated });
    }
    const imageDescriptor = { schemaVersion: 1, softwareVersion: identity.softwareVersion,
      sourceCommit: identity.sourceCommit, jar: { path: 'api.jar', ...jar }, apiContract: 'caption-hints.v2',
      sqlVersion: request.dataset.sqlVersion,
      dataset: { path: 'dataset.zip', ...dataset, releaseId: request.dataset.releaseId,
        preparationId: request.dataset.preparationId, ruleId: request.dataset.ruleId },
      notices: [...notices.entries()].map(([noticePath, summary]) => ({ path: noticePath, ...summary })) };
    const imageResult = await timed('image-build', () => buildImages({ repoRoot: root, artifactRoot, descriptor: imageDescriptor,
      outputParent: imageParent, endpoint: request.endpoint, platform: 'linux/arm64' }));
    const imageDirectory = imageResult.candidateDirectory;
    await safePath(imageDirectory, 'directory');
    const imageMarker = (await readBoundedJson(path.join(imageDirectory, 'candidate.json'))).value;
    const imageSha256 = (await fileDigest(path.join(imageDirectory, 'candidate.json'))).sha256;
    if (imageMarker.platform !== 'linux/arm64' || !Array.isArray(imageMarker.artifacts)
      || imageMarker.artifacts.length !== 1) fail('IMAGE_RESULT_INVALID');
    failurePhase = 'artifact-staging';
    const extensionCopy = await copyArtifact(zipSource, path.join(artifactRoot, 'extension.zip'), zip);
    await addArtifact(records, 'extension', extensionCopy, 'extension.zip', licenses,
      ['LexiFlow', 'extension-third-party'], { softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit,
        expectedDataset });
    for (const [role, relative, target] of [
      ['sql', 'infra/sqlite/schema.sql', 'schema.sql']]) {
      const source = path.join(root, relative); const summary = await fileDigest(source);
      const copied = await copyArtifact(source, path.join(artifactRoot, target), summary);
      await addArtifact(records, role, copied, target, licenses, ['LexiFlow']);
    }
    const manifestLicenses = licenses.map(item => ({ ...item }));
    for (const item of imageMarker.artifacts) {
      if (item.role !== 'runtime-image' || item.platform !== 'linux/arm64') fail('IMAGE_RESULT_INVALID');
      records.push({ role: item.role, platform: item.platform,
        licenseIds: licenseIds(licenses, 'LexiFlow', 'API-runtime') });
    }
    const descriptor = { schemaVersion: 2, buildIdentity: identity, softwareVersion: identity.softwareVersion,
      sourceCommit: identity.sourceCommit, apiContract: 'caption-hints.v2', sqlVersion: request.dataset.sqlVersion,
      dataset: { releaseId: request.dataset.releaseId, preparationId: request.dataset.preparationId, ruleId: request.dataset.ruleId },
      platforms: ['linux/arm64'], artifacts: records, licenses: manifestLicenses };
    const candidate = await timed('assemble', () => assemble({ repoRoot: root, imageCandidates: [{ directory: imageDirectory, sha256: imageSha256 }],
      artifactRoot, descriptor, outputParent: releaseParent }));
    failurePhase = 'candidate-verify';
    const candidateSha256 = (await fileDigest(path.join(candidate.candidateDirectory, 'candidate.json'))).sha256;
    const target = { candidateDirectory: candidate.candidateDirectory, candidateSha256 };
    const proof = await timed('candidate-verify', () => verify(target));
    if (proof.candidateSha256 !== candidateSha256 || proof.buildIdentity.buildId !== identity.buildId) fail('CANDIDATE_VERIFY_FAILED');
    failurePhase = 'handoff-finalize';
    assertBuildIdentityMatches(tagCheck(request.releaseTag, root), identity);
    if ((await readBoundedJson(requestFile)).sha256 !== requestSha256) fail('REQUEST_CHANGED');
    const finalHostBinding = hostBindingResolver();
    if (JSON.stringify(finalHostBinding) !== JSON.stringify(initialHostBinding)) fail('HOST_BINDING_DRIFT');
    const runtimeRequest = { target };
    const runtimeFile = path.join(quality, 'candidate-runtime-request.json');
    await writePrivate(runtimeFile, runtimeRequest);
    const runtimeSha256 = (await fileDigest(runtimeFile)).sha256;
    const handoff = { schemaVersion: 2, hostBinding: finalHostBinding, root,
      releaseTag: request.releaseTag, buildIdentity: identity, requestSha256, runtimeRequestSha256: runtimeSha256,
      requestFile, target };
    await writePrivate(path.join(quality, 'handoff.json'), handoff);
    if (process.env.GITHUB_ENV) {
      await safePath(process.env.GITHUB_ENV);
      const fd = await open(process.env.GITHUB_ENV, 'a');
      try { await fd.writeFile(`LEXIFLOW_CANDIDATE_RUNTIME_REQUEST=${runtimeFile}\n`); }
      finally { await fd.close(); }
    }
    return result({ status: 'PASS', stage: 'awaiting-native-formal' });
  } catch (error) { return result({ status: 'BLOCKED', reason: fixedReason(error) }); }
  finally { if (guard) await guard.close(); }
}

async function resumePromotionCore({ submissionId, releaseTag, publish = false, confirmedRecovery = false, root = ROOT,
  run = runCommand, identityReader = resolveBuildIdentity, tagChecker = checkReleaseTag,
  verifier = verifyReleaseCandidate, testOnlyHostBindingResolver = resolveHostBinding,
  environmentResolver = resolveReleaseExecutionEnvironment } = {}) {
  if (!UUID.test(submissionId || '') || !/^v[0-9]+\.[0-9]+\.[0-9]+$/.test(releaseTag || '')
    || typeof publish !== 'boolean' || typeof confirmedRecovery !== 'boolean') return { status: 'FAIL', reason: 'RESUME_INPUT_INVALID' };
  if (confirmedRecovery && !publish) return { status: 'FAIL', reason: 'RECOVERY_CONFIRMATION_REQUIRES_PUBLISH' };
  try {
    const quality = releaseWorkflowDirectory(root);
    const handoff = (await readBoundedJson(path.join(quality, 'handoff.json'))).value;
    const runtimeFile = path.join(quality, 'candidate-runtime-request.json');
    const runtime = await readBoundedJson(runtimeFile);
    const currentHostBinding = testOnlyHostBindingResolver();
    if (!exact(handoff, ['schemaVersion', 'hostBinding', 'root', 'releaseTag', 'buildIdentity', 'requestSha256',
      'runtimeRequestSha256', 'requestFile', 'target']) || handoff.schemaVersion !== 2 || !validHostBinding(handoff.hostBinding)
      || JSON.stringify(handoff.hostBinding) !== JSON.stringify(currentHostBinding)
      || handoff.root !== root || handoff.releaseTag !== releaseTag || handoff.runtimeRequestSha256 !== runtime.sha256
      || JSON.stringify(runtime.value) !== JSON.stringify({ target: handoff.target }))
      fail('HANDOFF_DRIFT');
    const original = await readBoundedJson(handoff.requestFile);
    if (original.sha256 !== handoff.requestSha256 || original.value.releaseTag !== releaseTag) fail('HANDOFF_DRIFT');
    const originalRequest = requestShape(original.value);
    assertBuildIdentityMatches(identityReader(root), handoff.buildIdentity);
    tagChecker(releaseTag, root);
    const target = await verifier(handoff.target);
    if (target.buildIdentity.buildId !== handoff.buildIdentity.buildId) fail('HANDOFF_DRIFT');
    const args = ['-m', 'ops.release.workflow.promote_verified_release', '--submission-id', submissionId,
      '--candidate-directory', handoff.target.candidateDirectory];
    if (publish) args.push('--publish');
    if (confirmedRecovery) args.push('--confirmed-recovery');
    const env = environmentResolver({ root, purpose: 'promotion' });
    if (publish) {
      if (!process.env.GITHUB_TOKEN || !process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN) fail('PUBLISH_TOKEN_MISSING');
      env.GITHUB_TOKEN = process.env.GITHUB_TOKEN;
      env.LEXIFLOW_RELEASE_CONFIG_TOKEN = process.env.LEXIFLOW_RELEASE_CONFIG_TOKEN;
      env.LEXIFLOW_RELEASE_PODMAN_ENDPOINT = originalRequest.endpoint;
      for (const key of ['GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_REF', 'GITHUB_SHA', 'GITHUB_EVENT_NAME']) {
        if (process.env[key]) env[key] = process.env[key];
      }
    }
    let output;
    try { output = await run('python3', args, { cwd: root, env, timeout: 1_800_000,
      logFile: path.join(quality, `promotion-${publish ? 'publish' : 'precheck'}-${randomUUID()}.log`) }); }
    catch (error) {
      let state;
      try {
        state = parseStrictJson(Buffer.from(error.publicOutput || '', 'utf8'));
      } catch { /* a crash or timeout after a remote write has unknown state */ }
      if (publish && state?.published === true)
        return { status: 'BLOCKED', reason: 'PUBLISHED_CLEANUP_BLOCKED', published: true,
          releaseId: state.release_id ?? null };
      if (publish && state?.published === 'unknown')
        return { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN', published: 'unknown',
          writeStage: state.write_stage ?? null, releaseId: state.release_id ?? null,
          ...(state.registry_role === 'runtime-image' ? { registryRole: state.registry_role } : {}) };
      if (publish && state?.published !== false)
        return { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN', published: 'unknown',
          writeStage: null, releaseId: null };
      return { status: 'BLOCKED', reason: fixedReason(error), ...(publish ? { published: false } : {}) };
    }
    let state;
    try { state = parseStrictJson(Buffer.from(output.trim(), 'utf8')); }
    catch {
      return publish
        ? { status: 'BLOCKED', reason: 'PUBLISH_STATE_UNKNOWN', published: 'unknown',
          writeStage: null, releaseId: null }
        : { status: 'BLOCKED', reason: 'PROMOTION_RESULT_INVALID' };
    }
    if (state.result !== 'PASS' || state.published !== publish
      || state.scope !== (publish ? 'promotion-publish' : 'promotion-precheck'))
      return { status: 'BLOCKED', reason: 'PROMOTION_RESULT_INVALID',
        ...(publish ? { published: state.published === true ? true : 'unknown' } : {}) };
    if (!publish && (typeof state.release_body !== 'string' || !state.release_body.trim()))
      return { status: 'BLOCKED', reason: 'PROMOTION_RESULT_INVALID' };
    return { status: 'PASS', stage: publish ? 'promotion-command-completed' : 'promotion-precheck-completed',
      ...(!publish && typeof state.release_body === 'string' ? { releaseBody: state.release_body } : {}) };
  } catch (error) { return { status: 'BLOCKED', reason: fixedReason(error) }; }
}


export async function resumePromotion(options = {}) {
  const started = performance.now();
  const phase = options.publish === true ? 'publish' : 'promotion-precheck';
  const value = await resumePromotionCore(options);
  if (options.diagnostics !== true) return value;
  const remoteWriteState = value.published === true ? 'published'
    : value.published === 'unknown' ? 'unknown'
      : value.published === false ? 'not-published'
        : phase === 'publish' && value.status === 'PASS' ? 'published'
          : phase === 'publish' ? 'not-started' : 'not-applicable';
  return { ...value, diagnostics: { phases: [{ phase, durationMs: Math.max(0, Math.round(performance.now() - started)) }],
    elapsedMs: Math.max(0, Math.round(performance.now() - started)), failurePhase: value.status === 'PASS' ? null : phase,
    remoteWriteState } };
}

export async function executeFormal(args, { root = ROOT, run = runCommand,
  environmentResolver = resolveReleaseExecutionEnvironment } = {}) {
  const invocation = resolveFormalInvocation(args, root);
  const env = environmentResolver({ root, purpose: 'formal' });
  const timeout = args[0] === 'validate' ? 5_400_000 : 1_800_000;
  const quality = releaseWorkflowDirectory(root);
  await safePath(quality, 'directory', true);
  await mkdir(quality, { recursive: true, mode: 0o700 });
  const action = args[0];
  return run(invocation.command, invocation.args, {
    cwd: invocation.cwd,
    env,
    timeout,
    logFile: path.join(quality, `formal-${action}-${randomUUID()}.log`),
  });
}

function formalResult(output) {
  try {
    const result = parseStrictJson(Buffer.from(output.trim(), 'utf8'));
    return ['PASS', 'BLOCKED', 'FAIL'].includes(result?.result) ? result : null;
  } catch { return null; }
}
function formalResultCode(status) {
  return status === 'PASS' ? 0 : status === 'BLOCKED' ? 2 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [mode, ...args] = process.argv.slice(2);
  if (mode === 'formal') {
    try {
      const output = await executeFormal(args);
      const result = formalResult(output);
      if (result) {
        process.stdout.write(`${output.trim()}\n`);
        process.exitCode = formalResultCode(result.result);
      } else {
        process.stdout.write(`${JSON.stringify({ result: 'FAIL', reason: 'FORMAL_RESULT_INVALID' })}\n`);
        process.exitCode = 1;
      }
    } catch (error) {
      const output = typeof error?.publicOutput === 'string' ? error.publicOutput.trim() : '';
      const result = output ? formalResult(output) : null;
      if (result && result.result !== 'PASS') {
        process.stdout.write(`${output}\n`);
        process.exitCode = formalResultCode(result.result);
      } else {
        process.stdout.write(`${JSON.stringify({ result: 'BLOCKED', reason: fixedReason(error) })}\n`);
        process.exitCode = 1;
      }
    }
  } else {
    const recoveryArgsValid = args.length === 4 && args[2] === '--publish' && args[3] === '--confirmed-recovery';
    const result = mode === 'prepare' && args.length === 1
      ? await prepareWorkflow(args[0], { diagnostics: true })
      : mode === 'resume' && (args.length === 2 || (args.length === 3 && args[2] === '--publish') || recoveryArgsValid)
        ? await resumePromotion({ submissionId: args[0], releaseTag: args[1], publish: args.length >= 3, confirmedRecovery: recoveryArgsValid, diagnostics: true })
        : mode === 'runner' && args.length === 1
          ? args[0] === '--help' ? { status: 'PASS', commands: ['doctor', 'deps', 'install', 'register', 'start', 'stop', 'status', 'setup'] }
            : await runRunnerCommand(args[0])
          : { status: 'FAIL', reason: 'ARGUMENTS_INVALID' };
    process.stdout.write(`${JSON.stringify(result)}\n`);
    if (result.status !== 'PASS') process.exitCode = 1;
  }
}
