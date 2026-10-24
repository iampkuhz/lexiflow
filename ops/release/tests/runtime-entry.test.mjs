import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { lifecycleFixture, runRuntime } from './lifecycle-fixture.mjs';

function testBin(t) {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-http-test-'));
  writeFileSync(path.join(directory, 'timeout'), '#!/bin/sh\nshift 2\nexec "$@"\n', { mode: 0o755 });
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function listen() {
  return new Promise((resolve, reject) => {
    const server = net.createServer((socket) => socket.once('data', () => socket.end(server.response)));
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function probe(entry, server, bin) {
  return new Promise((resolve, reject) => {
    const child = spawn('/bin/bash', ['-c', 'source "$1"; http_status_body /api/v1/runtime-status 127.0.0.1 "$2" && printf "%s" "$HTTP_BODY"', 'probe', entry, String(server.address().port)], {
      env: { ...process.env, LC_ALL: 'C', PATH: `${bin}${path.delimiter}${process.env.PATH}` },
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
    const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
    child.once('error', (error) => { clearTimeout(timer); reject(error); });
    child.once('exit', (status, signal) => { clearTimeout(timer); resolve({ status, signal, stdout, stderr }); });
  });
}

test('runtime Dockerfile is the locked one-image PID1 contract', async (t) => {
  const f = await lifecycleFixture(t);
  const dockerfile = (await import('node:fs/promises')).readFile(f.dockerfile, 'utf8');
  const source = await dockerfile;
  assert.match(source, /^FROM \$\{POSTGRES_RUNTIME_IMAGE\} AS runtime/mu);
  assert.match(source, /COPY --from=java-runtime \/opt\/java\/openjdk \/opt\/java\/openjdk/u);
  assert.match(source, /COPY .*runtime-entrypoint\.sh \/app\/runtime-entrypoint\.sh/u);
  assert.match(source, /LABEL io\.lexiflow\.image-role="runtime"/u);
  assert.match(source, /ENTRYPOINT \["\/app\/runtime-entrypoint\.sh"\]/u);
  assert.match(source, /CMD \["run"\]/u);
});

test('runtime entry validates the complete eight-field release identity and rejects duplicates', async (t) => {
  const f = await lifecycleFixture(t);
  const valid = runRuntime(f, 'read_identity; identity_line');
  assert.equal(valid.status, 0, valid.stderr);
  assert.equal(valid.stdout, f.identity);
  const duplicate = f.identity + 'softwareVersion=another-version\n';
  await (await import('node:fs/promises')).writeFile(f.identityPath, duplicate);
  const result = runRuntime(f, 'read_identity');
  assert.equal(result.status, 1);
  assert.equal(result.stderr.trim(), 'lexiflow-runtime: identity-invalid');
});

test('HTTP parser accepts observed reasonless Spring 200 and bounded chunked JSON', async (t) => {
  const f = await lifecycleFixture(t);
  const server = await listen();
  t.after(() => server.close());
  const bin = testBin(t);
  const body = '{"ready":false,"reason":"INITIALIZING"}';
  server.response = `HTTP/1.1 200 \r\nCache-Control: no-store\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nDate: Sat, 24 Oct 2026 15:41:48 GMT\r\n\r\n${Buffer.byteLength(body).toString(16)}\r\n${body}\r\n0\r\n\r\n`;
  const result = await probe(f.entry, server, bin);
  assert.equal(result.status, 0, `${result.error ?? ''}${result.stderr}`);
  assert.equal(result.stdout, body);
});

test('HTTP parser rejects duplicate framing headers and non-success status', async (t) => {
  const f = await lifecycleFixture(t);
  const server = await listen();
  t.after(() => server.close());
  const bin = testBin(t);
  const body = '{}';
  server.response = `HTTP/1.1 200 \r\nContent-Length: 2\r\ncontent-length: 2\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n${body}`;
  assert.notEqual((await probe(f.entry, server, bin)).status, 0);
  server.response = 'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n';
  assert.notEqual((await probe(f.entry, server, bin)).status, 0);
});
