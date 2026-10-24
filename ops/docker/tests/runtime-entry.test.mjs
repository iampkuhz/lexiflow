import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const entrypoint = fileURLToPath(new URL('../runtime-entrypoint.sh', import.meta.url));

function testBin(t) {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'lexiflow-runtime-test-'));
  writeFileSync(path.join(directory, 'timeout'), '#!/bin/sh\nshift 2\nexec "$@"\n', { mode: 0o755 });
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function listen() {
  return new Promise((resolve, reject) => {
    const server = net.createServer((socket) => {
      socket.once('data', () => socket.end(server.response));
    });
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function probe(server, bin) {
  return new Promise((resolve, reject) => {
    const child = spawn('bash', ['-c', 'source "$1"; http_status_body /api/v1/runtime-status 127.0.0.1 "$2" && printf "%s" "$HTTP_BODY"', '--', entrypoint, String(server.address().port)], {
      env: { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}` },
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
    child.once('error', reject);
    child.once('exit', (status) => resolve({ status, stdout, stderr }));
    setTimeout(() => child.kill('SIGKILL'), 5000).unref();
  });
}

test('HTTP parser accepts Spring status without reason phrase and keeps no-newline JSON body', async (t) => {
  const server = await listen();
  t.after(() => server.close());
  const bin = testBin(t);
  const body = '{"ready":false,"reason":"INITIALIZING"}';
  server.response = `HTTP/1.1 200 \r\nCache-Control: no-store\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nDate: Sat, 24 Oct 2026 15:41:48 GMT\r\n\r\n${Buffer.byteLength(body).toString(16)}\r\n${body}\r\n0\r\n\r\n`;

  const result = await probe(server, bin);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, body);
});

test('HTTP parser bounds and decodes chunked response bodies', async (t) => {
  const server = await listen();
  t.after(() => server.close());
  const bin = testBin(t);
  const body = '{"ready":true}';
  const cut = 5;
  server.response = `HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n${cut.toString(16)}\r\n${body.slice(0, cut)}\r\n${(body.length - cut).toString(16)}\r\n${body.slice(cut)}\r\n0\r\n\r\n`;

  const result = await probe(server, bin);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, body);
});

test('HTTP parser rejects duplicate and conflicting framing headers', async (t) => {
  const server = await listen();
  t.after(() => server.close());
  const bin = testBin(t);
  const body = '{}';
  server.response = `HTTP/1.1 200 OK\r\nContent-Length: 2\r\ncontent-length: 2\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n${body}`;

  const result = await probe(server, bin);
  assert.notEqual(result.status, 0);
  assert.equal(result.stdout, '');
});

test('TERM reaches and waits for an initialization child', async () => {
  const child = spawn('bash', ['-c', 'source "$1"; PG_PID=""; API_PID=""; INIT_PID=""; trap \'stop_children; exit 0\' TERM; sleep 60 & INIT_PID=$!; wait_active "$INIT_PID"', '--', entrypoint], {
    stdio: 'ignore',
  });
  await new Promise((resolve) => setTimeout(resolve, 250));
  const started = Date.now();
  child.kill('SIGTERM');
  const status = await new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  assert.ok(Date.now() - started < 5000);
  assert.equal(status.code, 0);
});
