import crypto from 'node:crypto';

const keys = Object.freeze(['http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
  'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY']);
const schemes = new Set(['http:', 'https:', 'socks4:', 'socks4a:', 'socks5:', 'socks5h:']);
function snapshot(environment) {
  const values = {};
  for (const key of keys) {
    const value = environment?.[key];
    if (value !== undefined && (typeof value !== 'string' || value.length > 16384 || /[\r\n\0]/.test(value)))
      throw new Error('SOURCE_NETWORK_ENVIRONMENT_INVALID');
    values[key] = value ?? null;
  }
  return values;
}
export function sourceNetworkFingerprint(environment) {
  return crypto.createHash('sha256').update(JSON.stringify(snapshot(environment))).digest('hex');
}
/** Only the source-fetch container receives these inherited values, never command-line values or saved config. */
export function sourceToolsNetwork(environment) {
  const original = snapshot(environment), values = {}, args = ['--http-proxy=false'];
  for (const key of keys) {
    const value = original[key];
    if (value === null || value === '') continue;
    let mapped = value;
    if (key.toLowerCase() !== 'no_proxy') {
      let url;
      try { url = new URL(value); } catch { throw new Error('SOURCE_NETWORK_ENVIRONMENT_INVALID'); }
      if (!schemes.has(url.protocol) || !url.hostname || url.search || url.hash
          || !['', '/'].includes(url.pathname)) throw new Error('SOURCE_NETWORK_ENVIRONMENT_INVALID');
      if (['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
        url.hostname = 'host.containers.internal';
        mapped = url.href;
      }
    }
    values[key] = mapped;
    args.push('-e', key);
  }
  return { args, environment: { ...environment, ...values } };
}
