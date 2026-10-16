import crypto from 'node:crypto';
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function header(name, size) {
  const b = Buffer.alloc(512);
  b.write(name, 0, 100, 'ascii'); b.write('0000600\0', 100, 8, 'ascii');
  b.write('0000000\0', 108, 8, 'ascii'); b.write('0000000\0', 116, 8, 'ascii');
  b.write(size.toString(8).padStart(11, '0') + '\0', 124, 12, 'ascii');
  b.write('00000000000\0', 136, 12, 'ascii'); b.fill(32, 148, 156); b.write('0', 156, 1, 'ascii');
  b.write('ustar\0', 257, 6, 'ascii'); b.write('00', 263, 2, 'ascii');
  b.write(b.reduce((n, x) => n + x, 0).toString(8).padStart(6, '0') + '\0 ', 148, 8, 'ascii');
  return b;
}
export function dockerSaveFixture({ role, identity, platform = 'linux/arm64' }) {
  const config = Buffer.from(JSON.stringify({ architecture: platform.split('/')[1], os: 'linux', config: { Labels: {
    'org.opencontainers.image.version': identity.softwareVersion, 'org.opencontainers.image.revision': identity.sourceCommit,
    'io.lexiflow.build-id': identity.buildId, 'io.lexiflow.source-sha256': identity.sourceSha256,
    'io.lexiflow.deployment-version': identity.baseVersion.toLowerCase(), 'io.lexiflow.image-role': role,
  } } }));
  const imageDigest = `sha256:${sha(config)}`, layer = Buffer.alloc(1024);
  const files = [
    ['manifest.json', Buffer.from(JSON.stringify([{ Config: `${imageDigest.slice(7)}.json`, RepoTags: [`localhost/lexiflow-${role}:${identity.baseVersion.toLowerCase()}`], Layers: ['layer/layer.tar'] }]))],
    [`${imageDigest.slice(7)}.json`, config], ['layer/layer.tar', layer],
    ['repositories', Buffer.from(JSON.stringify({ [`localhost/lexiflow-${role}`]: { [identity.baseVersion.toLowerCase()]: imageDigest.slice(7) } }))],
  ];
  const bytes = Buffer.concat([...files.flatMap(([name, data]) => [header(name, data.length), data, Buffer.alloc((512 - data.length % 512) % 512)]), Buffer.alloc(1024)]);
  return { bytes, imageDigest };
}
export function dockerSaveIdentity(bytes) {
  const manifest = JSON.parse(bytes.subarray(512, 512 + Number.parseInt(bytes.subarray(124, 136).toString('ascii').split('\0')[0], 8)).toString('utf8'));
  return { imageDigest: `sha256:${manifest[0].Config.replace(/\.json$/, '')}`, alias: manifest[0].RepoTags[0] };
}
