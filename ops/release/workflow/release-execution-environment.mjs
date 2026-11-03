import { spawnSync } from 'node:child_process';
import { accessSync, constants, lstatSync, readFileSync, realpathSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const fail = code => { throw new Error(code); };
const text = value => typeof value === 'string' ? value.trim() : '';
const absolute = value => value && path.isAbsolute(value) && !/[\u0000-\u001f\u007f]/.test(value);

function javaHome(source) {
  const configured = text(source.LEXIFLOW_JAVA_HOME);
  if (!absolute(configured)) fail('JAVA_ENVIRONMENT_MISSING');
  let home;
  try { home = realpathSync(configured); } catch { fail('JAVA_ENVIRONMENT_MISSING'); }
  const executable = path.join(home, 'bin/java');
  try {
    accessSync(executable, constants.X_OK);
  } catch (error) {
    if (/^JAVA_ENVIRONMENT_/.test(error?.message || '')) throw error;
    fail('JAVA_ENVIRONMENT_INVALID');
  }
  return home;
}

function requiredValue(source, name, code) {
  const value = text(source[name]);
  if (!value) fail(code);
  return value;
}

/** Validate the bounded fixed getconf result before using the OS-owned temp root. */
export function normalizeDarwinTempDirectory(result) {
  if (!result || result.error || result.status !== 0 || result.signal || typeof result.stdout !== 'string')
    fail('RELEASE_STANDARD_PATH_INVALID');
  const output = result.stdout;
  if (!output.endsWith('\n') || output.slice(0, -1).includes('\n')) fail('RELEASE_STANDARD_PATH_INVALID');
  const reported = output.slice(0, -1);
  if (!absolute(reported)) fail('RELEASE_STANDARD_PATH_INVALID');
  let canonical, info;
  try { canonical = realpathSync(reported); info = lstatSync(canonical); }
  catch { fail('RELEASE_STANDARD_PATH_INVALID'); }
  if (path.resolve(reported) !== path.normalize(reported).replace(/\/$/, '') || !info.isDirectory() || info.isSymbolicLink()
    || info.uid !== process.getuid() || (info.mode & 0o777) !== 0o700) fail('RELEASE_STANDARD_PATH_INVALID');
  return canonical;
}

function standardTempDirectory(user) {
  if (process.platform !== 'darwin') {
    try { return realpathSync('/tmp'); } catch { fail('RELEASE_STANDARD_PATH_INVALID'); }
  }
  const result = spawnSync('/usr/bin/getconf', ['DARWIN_USER_TEMP_DIR'], {
    encoding: 'utf8', timeout: 1500, maxBuffer: 4096, windowsHide: true,
    env: { PATH: '/usr/bin:/bin', HOME: user.homedir, USER: user.username, LOGNAME: user.username },
  });
  return normalizeDarwinTempDirectory(result);
}

/** Derive the immutable per-version handoff location without replacing older evidence. */
export function releaseWorkflowDirectory(root) {
  const file = path.join(root, 'ops/release/version.txt');
  let version;
  try {
    const info = lstatSync(file);
    if (!info.isFile() || info.isSymbolicLink() || info.size < 1 || info.size > 64) fail('RELEASE_VERSION_INVALID');
    version = readFileSync(file, 'utf8').trim();
  } catch { fail('RELEASE_VERSION_INVALID'); }
  if (!/^(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})$/.test(version)
    || version === '0.0.0' || version.split('.').some(part => Number(part) > 65535)) fail('RELEASE_VERSION_INVALID');
  return path.join(root, 'tmp/quality/release-workflow', version);
}

function ownedPrivateDirectory(value, code) {
  if (!absolute(value)) fail(code);
  try {
    const entry = lstatSync(value);
    if (!entry.isDirectory() || entry.isSymbolicLink() || realpathSync(value) !== value
      || entry.uid !== process.getuid() || (entry.mode & 0o022) !== 0 || (entry.mode & 0o200) === 0) fail(code);
  } catch (error) { if (error?.message === code) throw error; fail(code); }
}

/** Reuse only generated dependency/script caches, never user init/config or task-output caches. */
export function releaseGradleCacheEntries(root, cache) {
  ownedPrivateDirectory(cache, 'RELEASE_CACHE_INVALID');
  let properties;
  try { properties = readFileSync(path.join(root, 'backend/gradle/wrapper/gradle-wrapper.properties'), 'utf8'); }
  catch { fail('RELEASE_GRADLE_WRAPPER_INVALID'); }
  const versions = [...properties.matchAll(/^distributionUrl=.*\/gradle-([0-9]+\.[0-9]+\.[0-9]+)-bin\.zip\s*$/gm)];
  if (versions.length !== 1) fail('RELEASE_GRADLE_WRAPPER_INVALID');
  const entries = ['caches/modules-2', 'wrapper/dists'];
  for (const relative of entries) ownedPrivateDirectory(path.join(cache, relative), 'RELEASE_GRADLE_CACHE_INCOMPLETE');
  for (const relative of [`caches/${versions[0][1]}`, 'caches/jars-9']) {
    try { lstatSync(path.join(cache, relative)); }
    catch (error) { if (error.code === 'ENOENT') continue; fail('RELEASE_CACHE_INVALID'); }
    ownedPrivateDirectory(path.join(cache, relative), 'RELEASE_CACHE_INVALID');
    entries.push(relative);
  }
  return entries;
}

function privateCapabilities(root, source, purpose) {
  const java = javaHome(source);
  const env = { LEXIFLOW_JAVA_HOME: java, JAVA_HOME: java };

  const jdbc = requiredValue(source, 'LEXIFLOW_RELEASE_JDBC_URL', 'RELEASE_JDBC_URL_MISSING');
  if (!jdbc.startsWith('jdbc:sqlite:/') || /[\u0000\r\n?#]/.test(jdbc)
    || jdbc.slice('jdbc:sqlite:'.length).split('/').some(part => part === '.' || part === '..')
    || !jdbc.endsWith('/lexicon.db')) fail('RELEASE_JDBC_URL_INVALID');
  const redis = requiredValue(source, 'LEXIFLOW_REDIS_TEST_ENDPOINT', 'REDIS_TEST_ENDPOINT_MISSING');
  const endpoint = requiredValue(source, 'LEXIFLOW_RELEASE_ARM64_DOCKER_HOST', 'RELEASE_ARM64_ENDPOINT_MISSING');
  const gradle = requiredValue(source, 'LEXIFLOW_RELEASE_GRADLE_CACHE', 'RELEASE_GRADLE_CACHE_MISSING');
  const npm = requiredValue(source, 'LEXIFLOW_RELEASE_NPM_CACHE', 'RELEASE_NPM_CACHE_MISSING');
  if (!endpoint.startsWith('unix://')) fail('RELEASE_ARM64_ENDPOINT_INVALID');
  ownedPrivateDirectory(gradle, 'RELEASE_CACHE_INVALID');
  ownedPrivateDirectory(npm, 'RELEASE_CACHE_INVALID');
  for (const relative of ['caches/modules-2', 'wrapper/dists'])
    ownedPrivateDirectory(path.join(gradle, relative), 'RELEASE_GRADLE_CACHE_INCOMPLETE');
  if (purpose === 'prepare') releaseGradleCacheEntries(root, gradle);
  const candidate = path.join(releaseWorkflowDirectory(root), 'candidate-runtime-request.json');
  if (purpose !== 'prepare') {
    try {
      const info = lstatSync(candidate);
      if (!info.isFile() || info.isSymbolicLink() || !absolute(candidate)) fail('CANDIDATE_RUNTIME_REQUEST_INVALID');
    } catch (error) {
      if (/^CANDIDATE_RUNTIME_/.test(error?.message || '')) throw error;
      fail('CANDIDATE_RUNTIME_REQUEST_MISSING');
    }
  }
  Object.assign(env, {
    LEXIFLOW_RELEASE_JDBC_URL: jdbc,
    LEXIFLOW_REDIS_TEST_ENDPOINT: redis,
    LEXIFLOW_RELEASE_ARM64_DOCKER_HOST: endpoint,
    LEXIFLOW_RELEASE_GRADLE_CACHE: gradle,
    LEXIFLOW_RELEASE_NPM_CACHE: npm,
    ...(purpose === 'prepare' ? { GRADLE_USER_HOME: path.join(releaseWorkflowDirectory(root), 'gradle-home'),
      GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0',
      npm_config_globalconfig: '/dev/null', NPM_CONFIG_GLOBALCONFIG: '/dev/null',
      npm_config_cache: npm, NPM_CONFIG_CACHE: npm,
      npm_config_userconfig: path.join(releaseWorkflowDirectory(root), 'npmrc-empty'),
      NPM_CONFIG_USERCONFIG: path.join(releaseWorkflowDirectory(root), 'npmrc-empty') }
      : { LEXIFLOW_CANDIDATE_RUNTIME_REQUEST: candidate }),
  });
  return env;
}

/** Construct the one normalized release process environment; never copy ambient env wholesale. */
export function resolveReleaseExecutionEnvironment({ root, purpose, source = process.env } = {}) {
  const repository = path.resolve(root || process.cwd());
  if (!['prepare', 'promotion', 'formal'].includes(purpose)) fail('RELEASE_ENVIRONMENT_PURPOSE_INVALID');
  const configuredNodeDirectory = text(source.LEXIFLOW_RELEASE_NODE_BIN);
  if (!absolute(configuredNodeDirectory) || !/^22\./.test(process.versions.node)) fail('RELEASE_NODE22_REQUIRED');
  let configuredReal, runningReal, nodeBin;
  try {
    nodeBin = realpathSync(configuredNodeDirectory);
    if (!statSync(nodeBin).isDirectory()) fail('RELEASE_NODE22_REQUIRED');
    configuredReal = realpathSync(path.join(configuredNodeDirectory, 'node'));
    runningReal = realpathSync(process.execPath);
    accessSync(configuredReal, constants.X_OK);
  } catch { fail('RELEASE_NODE22_REQUIRED'); }
  if (configuredReal !== runningReal) fail('RELEASE_NODE22_MISMATCH');

  const pythonBin = path.join(repository, '.local/lexiflow-python/bin');
  try {
    if (!statSync(pythonBin).isDirectory()) fail('RELEASE_PYTHON_ENVIRONMENT_MISSING');
    accessSync(path.join(pythonBin, 'python3'), constants.X_OK);
  } catch (error) {
    if (/^RELEASE_PYTHON_/.test(error?.message || '')) throw error;
    fail('RELEASE_PYTHON_ENVIRONMENT_MISSING');
  }

  let user;
  try { user = os.userInfo(); } catch { fail('RELEASE_USER_INVALID'); }
  if (!user.username || !user.homedir || /[\u0000-\u001f\u007f]/.test(user.username)) fail('RELEASE_USER_INVALID');
  let home, temp;
  try { home = realpathSync(user.homedir); }
  catch { fail('RELEASE_STANDARD_PATH_INVALID'); }
  temp = standardTempDirectory(user);
  const env = {
    PATH: [pythonBin, nodeBin, '/opt/homebrew/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin'].join(path.delimiter),
    HOME: home,
    USER: user.username,
    LOGNAME: user.username,
    TMPDIR: temp,
    TEMP: temp,
    TMP: temp,
    LANG: 'en_US.UTF-8',
    LC_ALL: 'en_US.UTF-8',
    TZ: 'UTC',
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONHASHSEED: '0',
    ...privateCapabilities(repository, source, purpose),
  };
  if (purpose === 'prepare') {
    const check = (file, args, pattern, code) => {
      const result = spawnSync(file, args, { encoding: 'utf8', timeout: 5000, maxBuffer: 4096, windowsHide: true, env });
      const output = `${result.stdout || ''}\n${result.stderr || ''}`;
      if (result.error || result.status !== 0 || result.signal || !pattern.test(output)) fail(code);
    };
    check(configuredReal, ['--version'], /^v22\./m, 'RELEASE_NODE22_REQUIRED');
    check(path.join(pythonBin, 'python3'), ['-c',
      "import os, yaml; assert all(hasattr(os, name) for name in ('waitid', 'P_PID', 'WEXITED', 'WNOHANG', 'WNOWAIT', 'CLD_EXITED')); print('yaml-ready')"],
    /^yaml-ready$/m, 'RELEASE_PYTHON_ENVIRONMENT_INVALID');
    check(path.join(nodeBin, 'npm'), ['--version'], /^[0-9]+\.[0-9]+\.[0-9]+/m, 'RELEASE_NPM_REQUIRED');
    check(path.join(env.JAVA_HOME, 'bin/java'), ['-version'], /Temurin[^\n]*25\./i, 'JAVA_ENVIRONMENT_INVALID');
    const userconfig = env.npm_config_userconfig;
    try { if (lstatSync(userconfig).isSymbolicLink()) fail('RELEASE_NPM_CONFIG_INVALID'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (purpose === 'formal') {
    for (const key of ['CODEX_HOME', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID'])
      if (typeof source[key] === 'string' && source[key]) env[key] = source[key];
  }
  return env;
}

const TASK_ID = /^[A-Z][A-Z0-9]*-TSK-[A-Z0-9]+-[0-9]+$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Only map the four reviewed acceptance operations; caller supplies no interpreter or repository root. */
export function resolveFormalInvocation(args, root) {
  if (!Array.isArray(args) || typeof root !== 'string' || !path.isAbsolute(root)) fail('FORMAL_ARGUMENTS_INVALID');
  const base = ['-m', 'scripts.acceptance'];
  if (args.length === 2 && args[0] === 'submit' && typeof args[1] === 'string' && TASK_ID.test(args[1]))
    return { command: 'python3', args: [...base, 'submit', '--task-id', args[1], '--release-handoff'], cwd: root };
  if (args.length === 2 && args[0] === 'validate' && typeof args[1] === 'string' && UUID.test(args[1]))
    return { command: 'python3', args: [...base, 'validate', '--submission-id', args[1]], cwd: root };
  if ((args.length === 2 || args.length === 3) && args[0] === 'review' && typeof args[1] === 'string' && UUID.test(args[1])
    && (args.length === 2 || (typeof args[2] === 'string' && path.isAbsolute(args[2]) && !/[\u0000-\u001f\u007f]/.test(args[2]))))
    return { command: 'python3', args: [...base, 'review', '--validation-id', args[1], ...(args.length === 3 ? ['--findings-json', args[2]] : [])], cwd: root };
  if (args.length === 2 && args[0] === 'check' && typeof args[1] === 'string' && UUID.test(args[1]))
    return { command: 'python3', args: [...base, 'check', '--submission-id', args[1]], cwd: root };
  fail('FORMAL_ARGUMENTS_INVALID');
}
