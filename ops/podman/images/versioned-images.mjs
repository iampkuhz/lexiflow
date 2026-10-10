import { parseVersion } from '../../release/integrity/source-build-identity.mjs';

const roles = new Set(['api', 'postgres', 'source-tools']);
const fail = code => { throw new Error(code); };
const validRole = role => { if (!roles.has(role)) fail('VERSIONED_IMAGE_ROLE_INVALID'); };

/** 部署槽位仅消费软件基础版本，不接受源码日期、commit 或 dirty 摘要。 */
export function deploymentImageTag(baseVersion) {
  try { if (typeof baseVersion !== 'string' || parseVersion(baseVersion) !== baseVersion) fail('VERSIONED_IMAGE_VERSION_INVALID'); }
  catch { fail('VERSIONED_IMAGE_VERSION_INVALID'); }
  return baseVersion.toLowerCase();
}

export function versionedImageReference(role, baseVersion) {
  validRole(role);
  return `localhost/lexiflow-${role}:${deploymentImageTag(baseVersion)}`;
}

export function versionedImageLabels(role, baseVersion) {
  validRole(role);
  const tag = deploymentImageTag(baseVersion);
  return ['--label', `io.lexiflow.image-role=${role}`, '--label', `io.lexiflow.deployment-version=${tag}`];
}

const imageIdPattern = /^(?:sha256:)?[a-f0-9]{64}$/;
const canonicalImageId = value => typeof value === 'string' && imageIdPattern.test(value) ? `sha256:${value.replace(/^sha256:/, '')}` : null;
const containerIdPattern = /^[a-f0-9]{64}$/;
function output(result) {
  if (typeof result === 'string') return result;
  return result?.stdout ?? result?.output ?? '';
}
function checked(result, code) {
  // runCommand 在成功时返回捕获文本；非零退出由带 commandExitCode 的异常报告。
  if (typeof result === 'string') return result;
  if (!result || result.commandExitCode !== 0) fail(code);
  const value = output(result);
  if (typeof value !== 'string') fail(code);
  return value;
}
async function call(run, args, code) {
  try { return await run('podman', args, { capture: true }); }
  catch { fail(code); }
}
function parseJson(text, code) {
  try { return JSON.parse(text); } catch { fail(code); }
}
function oneObject(text, code) {
  const value = parseJson(text, code);
  if (!Array.isArray(value) || value.length !== 1 || !value[0] || typeof value[0] !== 'object') fail(code);
  return value[0];
}
function imageIdentity(image, role, tag) {
  if (typeof image.Id !== 'string' || !imageIdPattern.test(image.Id)
      || image.Labels?.['io.lexiflow.image-role'] !== role
      || image.Labels?.['io.lexiflow.deployment-version'] !== tag) fail('VERSIONED_IMAGE_OWNERSHIP_MISMATCH');
  return canonicalImageId(image.Id);
}
function projectLabels(labels, project) {
  const compose = labels['com.docker.compose.project'];
  const podman = labels['io.podman.compose.project'];
  if (compose === undefined && podman === undefined
      || compose !== undefined && compose !== project || podman !== undefined && podman !== project
      || compose !== undefined && podman !== undefined && compose !== podman) fail('VERSIONED_IMAGE_CONTAINER_MISMATCH');
}
function serviceLabels(labels, role) {
  const compose = labels['com.docker.compose.service'];
  const podman = labels['io.podman.compose.service'];
  if (compose === undefined && podman === undefined
      || compose !== undefined && compose !== role || podman !== undefined && podman !== role
      || compose !== undefined && podman !== undefined && compose !== podman) fail('VERSIONED_IMAGE_CONTAINER_MISMATCH');
}

/** 先完整核验引用者，再移除本安装容器和精确 tag；不删除卷或其他版本槽位。 */
export async function removeVersionedImage({ run, role, baseVersion, installationId = null } = {}) {
  validRole(role);
  const tag = deploymentImageTag(baseVersion);
  const exactRef = `localhost/lexiflow-${role}:${tag}`;
  if (typeof run !== 'function') fail('VERSIONED_IMAGE_RUNNER_INVALID');

  let exists;
  try { exists = await run('podman', ['image', 'exists', exactRef], { capture: true }); }
  catch (error) {
    if (error.commandExitCode === 1) return { removed: false, imageId: null, removedContainerIds: [] };
    fail('VERSIONED_IMAGE_CHECK_FAILED');
  }
  if (exists?.commandExitCode === 1) return { removed: false, imageId: null, removedContainerIds: [] };
  checked(exists, 'VERSIONED_IMAGE_CHECK_FAILED');

  const image = oneObject(checked(await call(run, ['image', 'inspect', exactRef, '--format', 'json'], 'VERSIONED_IMAGE_INSPECT_FAILED'), 'VERSIONED_IMAGE_INSPECT_FAILED'), 'VERSIONED_IMAGE_INSPECT_FAILED');
  const imageId = imageIdentity(image, role, tag);
  // Podman 的 ancestor 过滤器使用无前缀完整 ID；sha256: 前缀会静默漏掉引用者。
  const ps = checked(await call(run, ['ps', '-a', '--no-trunc', '--filter', `ancestor=${imageId.slice(7)}`, '--format', '{{.ID}}'], 'VERSIONED_IMAGE_CONTAINER_QUERY_FAILED'), 'VERSIONED_IMAGE_CONTAINER_QUERY_FAILED');
  const ids = [...new Set(ps.split(/\r?\n/).map(value => value.trim()).filter(Boolean))];
  const project = `lexiflow-local-${installationId}`;
  const inspected = [];

  // 完整预检所有引用者；不能先删除部分自有容器，再发现其他安装也在使用。
  for (const listedId of ids) {
    if (!containerIdPattern.test(listedId)) fail('VERSIONED_IMAGE_CONTAINER_MISMATCH');
    const container = oneObject(checked(await call(run, ['container', 'inspect', listedId, '--format', 'json'], 'VERSIONED_IMAGE_CONTAINER_INSPECT_FAILED'), 'VERSIONED_IMAGE_CONTAINER_INSPECT_FAILED'), 'VERSIONED_IMAGE_CONTAINER_INSPECT_FAILED');
    const actualId = container.Id?.replace(/^\//, '');
    if (!containerIdPattern.test(actualId ?? '') || actualId !== listedId || canonicalImageId(container.Image) !== imageId) fail('VERSIONED_IMAGE_CONTAINER_MISMATCH');
    const labels = container.Config?.Labels;
    if (!labels || typeof labels !== 'object') fail('VERSIONED_IMAGE_CONTAINER_MISMATCH');
    if (role === 'source-tools') fail('VERSIONED_IMAGE_SOURCE_TOOLS_IN_USE');
    if (typeof installationId !== 'string' || !/^[a-f0-9]{32}$/.test(installationId)) fail('VERSIONED_IMAGE_INSTALLATION_REQUIRED');
    if (labels['lexiflow.installation'] !== installationId) fail('VERSIONED_IMAGE_CONTAINER_MISMATCH');
    projectLabels(labels, project);
    serviceLabels(labels, role);
    inspected.push({ id: actualId, running: container.State?.Running === true });
  }

  const stopCalls = [];
  for (const container of inspected) {
    if (container.running) checked(await call(run, ['stop', '--time', '30', container.id], 'VERSIONED_IMAGE_CONTAINER_STOP_FAILED'), 'VERSIONED_IMAGE_CONTAINER_STOP_FAILED');
    checked(await call(run, ['rm', container.id], 'VERSIONED_IMAGE_CONTAINER_REMOVE_FAILED'), 'VERSIONED_IMAGE_CONTAINER_REMOVE_FAILED');
    stopCalls.push(container.id);
  }

  // 清理前再次核对精确 tag 与完整引用集合；rmi 不使用 force。
  const currentImage = oneObject(checked(await call(run, ['image', 'inspect', exactRef, '--format', 'json'], 'VERSIONED_IMAGE_RECHECK_FAILED'), 'VERSIONED_IMAGE_RECHECK_FAILED'), 'VERSIONED_IMAGE_RECHECK_FAILED');
  if (imageIdentity(currentImage, role, tag) !== imageId) fail('VERSIONED_IMAGE_DRIFT');
  const currentPs = checked(await call(run, ['ps', '-a', '--no-trunc', '--filter', `ancestor=${imageId.slice(7)}`, '--format', '{{.ID}}'], 'VERSIONED_IMAGE_CONTAINER_QUERY_FAILED'), 'VERSIONED_IMAGE_CONTAINER_QUERY_FAILED');
  if (currentPs.split(/\r?\n/).map(value => value.trim()).filter(Boolean).length) fail('VERSIONED_IMAGE_DRIFT');
  checked(await call(run, ['rmi', exactRef], 'VERSIONED_IMAGE_REMOVE_FAILED'), 'VERSIONED_IMAGE_REMOVE_FAILED');
  return { removed: true, imageId, removedContainerIds: stopCalls };
}
