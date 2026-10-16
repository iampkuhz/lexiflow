const allowed = new Map([
  ['docker.io/library/eclipse-temurin:25-jre', 'java'],
  ['docker.io/library/postgres:17-bookworm', 'postgres'],
]);
const idPattern = /^(?:sha256:)?[a-f0-9]{64}$/;

function fail(code) { throw new Error(code); }
function checked(result, code) {
  if (typeof result === 'string') return result;
  if (!result || result.commandExitCode !== 0) fail(code);
  const value = result.stdout ?? result.output ?? '';
  if (typeof value !== 'string') fail(code);
  return value;
}
async function invoke(run, args, code) {
  try { return await run('podman', args, { capture: true }); }
  catch (error) { fail(error?.commandInterrupted === true ? 'DEPLOYMENT_OPERATION_CANCELLED' : code); }
}
function inspectObject(text) {
  let parsed;
  try { parsed = JSON.parse(text); } catch { fail('SOURCE_BASE_IMAGE_INVALID'); }
  if (!Array.isArray(parsed) || parsed.length !== 1 || !parsed[0] || typeof parsed[0] !== 'object' || Array.isArray(parsed[0])) fail('SOURCE_BASE_IMAGE_INVALID');
  return parsed[0];
}
function envValue(env, key) {
  if (!Array.isArray(env)) fail('SOURCE_BASE_IMAGE_INVALID');
  const entries = env.filter(item => typeof item === 'string' && item.startsWith(`${key}=`));
  if (entries.length !== 1) fail('SOURCE_BASE_IMAGE_INVALID');
  return entries[0].slice(key.length + 1);
}
function validate(image, kind) {
  if (typeof image.Id !== 'string' || !idPattern.test(image.Id)
      || image.Os !== 'linux' || image.Architecture !== 'arm64') fail('SOURCE_BASE_IMAGE_INVALID');
  const env = image.Config?.Env;
  if (kind === 'java') {
    if (!/^jdk-25(?:\.[0-9]+)*\+[0-9]+$/.test(envValue(env, 'JAVA_VERSION'))) fail('SOURCE_BASE_IMAGE_INVALID');
  } else if (envValue(env, 'PG_MAJOR') !== '17' || !/^17\..+/.test(envValue(env, 'PG_VERSION'))) {
    fail('SOURCE_BASE_IMAGE_INVALID');
  }
  return `sha256:${image.Id.replace(/^sha256:/, '')}`;
}
async function inspect(run, reference) {
  const result = await invoke(run, ['image', 'inspect', reference, '--format', 'json'], 'SOURCE_BASE_IMAGE_UNAVAILABLE');
  return inspectObject(checked(result, 'SOURCE_BASE_IMAGE_UNAVAILABLE'));
}

/** Resolve only the fixed official source-build bases; return an immutable full image ID. */
export async function resolveSourceBaseImage({ run, reference } = {}) {
  const kind = allowed.get(reference);
  if (!kind) fail('SOURCE_BASE_IMAGE_INVALID');
  if (typeof run !== 'function') fail('SOURCE_BASE_IMAGE_UNAVAILABLE');

  let exists;
  try { exists = await run('podman', ['image', 'exists', reference], { capture: true }); }
  catch (error) {
    if (error?.commandInterrupted === true) fail('DEPLOYMENT_OPERATION_CANCELLED');
    if (error?.commandExitCode !== 1) fail('SOURCE_BASE_IMAGE_UNAVAILABLE');
    exists = { commandExitCode: 1 };
  }
  if (exists?.commandExitCode === 1) {
    checked(await invoke(run, ['pull', '--platform', 'linux/arm64', reference], 'SOURCE_BASE_IMAGE_UNAVAILABLE'), 'SOURCE_BASE_IMAGE_UNAVAILABLE');
  } else {
    checked(exists, 'SOURCE_BASE_IMAGE_UNAVAILABLE');
  }

  const initial = validate(await inspect(run, reference), kind);
  // Re-read the mutable tag immediately before returning; consumers receive only its immutable ID.
  const final = validate(await inspect(run, reference), kind);
  if (initial !== final) fail('SOURCE_BASE_IMAGE_CHANGED');
  return initial;
}
