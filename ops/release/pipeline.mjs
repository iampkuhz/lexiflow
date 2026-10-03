#!/usr/bin/env node
import { constants, realpathSync } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildCandidateImages } from './build-execution.mjs';
import { assembleReleaseCandidate } from './candidate.mjs';
import { verifyReleaseCandidate } from './verified-candidate.mjs';

const MAX_REQUEST_BYTES = 2_000_000;
const OPERATIONS = new Set(['images', 'assemble', 'verify']);

async function readRequest(requestPath) {
  if (!path.isAbsolute(requestPath) || /[\u0000-\u001f\u007f]/.test(requestPath)) throw new Error('PIPELINE_REQUEST_INVALID');
  const absolute = path.resolve(requestPath);
  let cursor = path.parse(absolute).root;
  for (const part of absolute.slice(cursor.length).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const info = await lstat(cursor).catch(() => { throw new Error('PIPELINE_REQUEST_INVALID'); });
    if (info.isSymbolicLink()) throw new Error('PIPELINE_REQUEST_INVALID');
  }
  const before = await lstat(absolute).catch(() => { throw new Error('PIPELINE_REQUEST_INVALID'); });
  if (!before.isFile()) throw new Error('PIPELINE_REQUEST_INVALID');
  if (before.size > MAX_REQUEST_BYTES) throw new Error('PIPELINE_REQUEST_INVALID');
  let handle;
  try {
    const flags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0);
    handle = await open(absolute, flags);
    const after = await handle.stat();
    if (!after.isFile() || after.size > MAX_REQUEST_BYTES) throw new Error('PIPELINE_REQUEST_INVALID');
    const buffer = Buffer.alloc(MAX_REQUEST_BYTES + 1);
    let total = 0;
    while (total < buffer.length) {
      const { bytesRead } = await handle.read(buffer, total, buffer.length - total, null);
      if (!bytesRead) break;
      total += bytesRead;
    }
    if (total > MAX_REQUEST_BYTES || total !== after.size) throw new Error('PIPELINE_REQUEST_INVALID');
    return JSON.parse(buffer.subarray(0, total).toString('utf8'));
  } catch {
    throw new Error('PIPELINE_REQUEST_INVALID');
  } finally {
    if (handle) await handle.close().catch(() => {});
  }
}

function emit(payload) { process.stdout.write(`${JSON.stringify(payload)}\n`); }

export async function runPipeline(argv) {
  const mode = argv.length >= 1 && OPERATIONS.has(argv[0]) ? argv[0] : null;
  if (argv.length !== 2 || !mode || !path.isAbsolute(argv[1])) {
    emit({ status: 'FAIL', scope: mode === 'verify' ? 'candidate-integrity-only' : 'candidate-only', operation: mode, reason: 'PIPELINE_ARGUMENTS_INVALID' });
    return 1;
  }
  let request;
  try { request = await readRequest(argv[1]); }
  catch {
    emit({ status: 'FAIL', scope: mode === 'verify' ? 'candidate-integrity-only' : 'candidate-only', operation: mode, reason: 'PIPELINE_REQUEST_INVALID' });
    return 1;
  }
  try {
    if (mode === 'verify') {
      const proof = await verifyReleaseCandidate(request);
      emit({ status: 'PASS', scope: 'candidate-integrity-only', operation: 'verify', ...proof });
    } else {
      const result = mode === 'images' ? await buildCandidateImages(request) : await assembleReleaseCandidate(request);
      emit({ status: 'PASS', scope: 'candidate-only', operation: mode, candidateDirectory: result.candidateDirectory });
    }
    return 0;
  } catch {
    emit({ status: 'FAIL', scope: mode === 'verify' ? 'candidate-integrity-only' : 'candidate-only', operation: mode, reason: 'PIPELINE_OPERATION_FAILED' });
    return 1;
  }
}

function invokedDirectly() {
  try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); }
  catch { return false; }
}

if (invokedDirectly()) {
  runPipeline(process.argv.slice(2)).then((code) => { process.exitCode = code; }, () => {
    emit({ status: 'FAIL', scope: 'candidate-only', operation: null, reason: 'PIPELINE_OPERATION_FAILED' });
    process.exitCode = 1;
  });
}
