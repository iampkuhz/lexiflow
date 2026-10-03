// 在线正式版查询是可失败的只读补充，不改变本机安装或把未知误报成最新。
import https from 'node:https';
import { parseVersion } from '../release/version.mjs';

export function latestRelease(body) {
  if (!body || body.draft !== false || body.prerelease !== false || typeof body.tag_name !== 'string' || !body.tag_name.startsWith('v')) return null;
  try {
    const version = parseVersion(body.tag_name.slice(1));
    return !version.includes('-') && body.tag_name === `v${version}` ? version : null;
  } catch { return null; }
}
export function githubLatestRelease() {
  return new Promise(resolve => {
    let settled = false;
    const finish = version => { if (!settled) { settled = true; resolve(version); } };
    const request = https.get('https://api.github.com/repos/iampkuhz/lexiflow/releases/latest', {
      agent: false, headers: { 'User-Agent': 'LexiFlow-local', Accept: 'application/vnd.github+json' },
    }, response => {
      if (response.statusCode !== 200) { finish(null); response.destroy(); return; }
      const chunks = []; let bytes = 0;
      response.on('data', chunk => { bytes += chunk.length; if (bytes > 131072) { finish(null); response.destroy(); } else chunks.push(chunk); });
      response.on('end', () => { try { finish(latestRelease(JSON.parse(Buffer.concat(chunks).toString('utf8')))); } catch { finish(null); } });
      response.on('error', () => finish(null)); response.on('aborted', () => finish(null));
    });
    const timer = setTimeout(() => { finish(null); request.destroy(); }, 3000);
    request.on('error', () => finish(null)); request.on('close', () => clearTimeout(timer));
  });
}
export function versionSummary(state, target, latest = null) {
  return { installed: state?.version ?? null, local: target.softwareVersion, latestRelease: latest,
    needsUpgrade: !state ? null : state.buildIdentity?.buildId !== target.buildId };
}
