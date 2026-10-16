// Candidate cleanup removes only its stable slot alias, never a shared image ID.
import { deploymentImageTag } from './versioned-images.mjs';
import { slotImageNamespace } from '../deployment-slots.mjs';

const fail = code => { throw new Error(code); };
export function candidateImageAlias(role, baseVersion) {
  if (!['api', 'postgres'].includes(role)) fail('CANDIDATE_ALIAS_ARGUMENTS_INVALID');
  return `localhost/${slotImageNamespace('release-validation')}-${role}:${deploymentImageTag(baseVersion)}`;
}
const fullId = value => typeof value === 'string' && /^(?:sha256:)?[a-f0-9]{64}$/.test(value)
  ? `sha256:${value.replace(/^sha256:/, '')}` : null;
function output(result, code) {
  if (typeof result === 'string') return result;
  if (result?.commandExitCode !== 0 || typeof result?.stdout !== 'string') fail(code);
  return result.stdout;
}
async function call(run, args, code) {
  try { return output(await run('podman', args, { capture: true }), code); }
  catch { fail(code); }
}
function inspected(text) {
  let rows;
  try { rows = JSON.parse(text); } catch { fail('CANDIDATE_ALIAS_INSPECT_INVALID'); }
  if (!Array.isArray(rows) || rows.length !== 1 || !rows[0] || typeof rows[0] !== 'object') fail('CANDIDATE_ALIAS_INSPECT_INVALID');
  return rows[0];
}
function owns(image, alias, role, tag, allowedImages) {
  const id = fullId(image.Id || image.ID);
  const labels = image.Config?.Labels || image.Labels || {};
  if (image.Config?.Labels && image.Labels && Object.keys(labels).some(key => image.Labels[key] !== labels[key]))
    fail('CANDIDATE_ALIAS_OWNERSHIP_MISMATCH');
  const match = allowedImages.some(candidate => candidate.imageDigest === id
    && candidate.buildIdentity?.buildId === labels['io.lexiflow.build-id']
    && candidate.buildIdentity?.sourceSha256 === labels['io.lexiflow.source-sha256']
    && candidate.buildIdentity?.sourceCommit === labels['org.opencontainers.image.revision']
    && candidate.buildIdentity?.softwareVersion === labels['org.opencontainers.image.version']);
  if (!id || !match || labels['io.lexiflow.image-role'] !== role
    || labels['io.lexiflow.deployment-version'] !== tag
    || !Array.isArray(image.RepoTags) || !image.RepoTags.includes(alias)) fail('CANDIDATE_ALIAS_OWNERSHIP_MISMATCH');
  return id;
}

/** Call after owned candidate containers/volumes are removed; absence is harmless before a load. */
export async function removeCandidateImageAlias({ run, role, baseVersion, allowedImages }) {
  if (typeof run !== 'function' || !['api', 'postgres'].includes(role)
    || !Array.isArray(allowedImages) || !allowedImages.length
    || allowedImages.some(v => !fullId(v?.imageDigest) || !v?.buildIdentity)) fail('CANDIDATE_ALIAS_ARGUMENTS_INVALID');
  const tag = deploymentImageTag(baseVersion);
  const alias = candidateImageAlias(role, baseVersion);
  let exists;
  try { exists = await run('podman', ['image', 'exists', alias], { capture: true }); }
  catch (error) {
    if (error.commandExitCode === 1) return { removed: false, alias };
    fail('CANDIDATE_ALIAS_CHECK_FAILED');
  }
  if (exists?.commandExitCode === 1) return { removed: false, alias };
  output(exists, 'CANDIDATE_ALIAS_CHECK_FAILED');
  const initial = inspected(await call(run, ['image', 'inspect', alias, '--format', 'json'], 'CANDIDATE_ALIAS_INSPECT_FAILED'));
  const imageId = owns(initial, alias, role, tag, allowedImages);
  const latest = inspected(await call(run, ['image', 'inspect', alias, '--format', 'json'], 'CANDIDATE_ALIAS_INSPECT_FAILED'));
  if (owns(latest, alias, role, tag, allowedImages) !== imageId) fail('CANDIDATE_ALIAS_DRIFT');
  await call(run, ['image', 'untag', alias], 'CANDIDATE_ALIAS_UNTAG_FAILED');
  return { removed: true, alias, imageId };
}
