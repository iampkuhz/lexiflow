import { readZipEntries } from './extension-archive-integrity.mjs';
import { assertBuildIdentityMatches } from './source-build-identity.mjs';

const JAR_IDENTITY = 'META-INF/lexiflow-build.json';
const JAR_VERSION = 'META-INF/lexiflow-version.txt';

/** 只读取已构建制品的内嵌元数据；当前源码身份不能替任意输入制品背书。 */
export async function verifyJarIdentity(filePath, identity) {
  const entries = await readZipEntries(filePath, [JAR_IDENTITY, JAR_VERSION]);
  try {
    assertBuildIdentityMatches(JSON.parse(entries.get(JAR_IDENTITY).toString('utf8')), identity);
    if (entries.get(JAR_VERSION).toString('utf8') !== `${identity.softwareVersion}\n`) throw new Error();
  } catch { throw new Error('JAR_IDENTITY_MISMATCH'); }
}

export async function verifyExtensionIdentity(filePath, identity, expectedDataset) {
  const entries = await readZipEntries(filePath, ['build-identity.json', 'manifest.json', 'expected-dataset.json']);
  try {
    assertBuildIdentityMatches(JSON.parse(entries.get('build-identity.json').toString('utf8')), identity);
    const manifest = JSON.parse(entries.get('manifest.json').toString('utf8'));
    const dataset = JSON.parse(entries.get('expected-dataset.json').toString('utf8'));
    if (manifest.version !== identity.chromeVersion || manifest.version_name !== identity.softwareVersion
      || JSON.stringify(manifest.permissions) !== JSON.stringify(['storage'])
      || JSON.stringify(manifest.host_permissions) !== JSON.stringify(['http://127.0.0.1:18080/*'])
      || !dataset || typeof dataset !== 'object' || Array.isArray(dataset)
      || Object.keys(dataset).sort().join('\0') !== ['releaseId', 'preparationId', 'ruleId', 'sqlVersion', 'sha256'].sort().join('\0')
      || !/^[a-f0-9]{64}$/.test(dataset.sha256)
      || ![dataset.releaseId, dataset.preparationId, dataset.ruleId, dataset.sqlVersion].every(value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value))
      || expectedDataset && ['releaseId', 'preparationId', 'ruleId', 'sqlVersion', 'sha256'].some(key => dataset[key] !== expectedDataset[key])) throw new Error();
  } catch { throw new Error('EXTENSION_IDENTITY_MISMATCH'); }
}
