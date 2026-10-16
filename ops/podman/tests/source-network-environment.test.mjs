import test from 'node:test';
import assert from 'node:assert/strict';
import { sourceToolsNetwork, sourceNetworkFingerprint } from '../environment/source-network-environment.mjs';

test('loopback standard proxies use host gateway without values in argv or persistent config', () => {
  const source = { PATH: '/bin', https_proxy: 'http://user:private@127.0.0.1:7890',
    ALL_PROXY: 'socks5h://[::1]:7891', no_proxy: 'localhost,127.0.0.1', PRIVATE_OTHER: 'untouched' };
  const result = sourceToolsNetwork(source);
  assert.equal(result.environment.https_proxy, 'http://user:private@host.containers.internal:7890/');
  assert.equal(result.environment.ALL_PROXY, 'socks5h://host.containers.internal:7891');
  assert.equal(result.environment.no_proxy, source.no_proxy);
  assert.deepEqual(result.args, ['--http-proxy=false', '-e', 'https_proxy', '-e', 'no_proxy', '-e', 'ALL_PROXY']);
  assert.equal(result.args.some(x => /private|7890/.test(x)), false);
  assert.equal(source.https_proxy.includes('127.0.0.1'), true);
});
test('non-loopback inherited routes retain exact values and absent routes never use engine defaults', () => {
  const source = { HTTPS_PROXY: 'http://proxy.example:3128', PATH: '/bin' };
  assert.equal(sourceToolsNetwork(source).environment.HTTPS_PROXY, source.HTTPS_PROXY);
  assert.deepEqual(sourceToolsNetwork({}).args, ['--http-proxy=false']);
});
test('route snapshot detects changes without returning credentials', () => {
  const source = { https_proxy: 'http://private@localhost:7890' };
  const before = sourceNetworkFingerprint(source);
  assert.match(before, /^[a-f0-9]{64}$/);
  assert.equal(before, sourceNetworkFingerprint({ ...source, PATH: '/different' }));
  source.https_proxy = 'http://private@localhost:7891';
  assert.notEqual(before, sourceNetworkFingerprint(source));
});
test('malformed and unsupported routes fail with a safe fixed reason', () => {
  for (const value of ['file:///secret', 'http://proxy/path', 'http://proxy/?token=private', 'not a url', 'http://proxy\nprivate'])
    assert.throws(() => sourceToolsNetwork({ https_proxy: value }), { message: 'SOURCE_NETWORK_ENVIRONMENT_INVALID' });
});
