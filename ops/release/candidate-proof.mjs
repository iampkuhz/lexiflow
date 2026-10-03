import { realpathSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyReleaseCandidate } from './verified-candidate.mjs';
import { resolveBuildIdentity, assertBuildIdentityMatches } from './version.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const sha = /^[a-f0-9]{64}$/;
export function parseStrict(source) {
  let i = 0;
  const ws = () => { while (/[ \t\r\n]/.test(source[i] ?? '')) i += 1; };
  const string = () => {
    const start = i++;
    while (i < source.length) {
      if (source[i] === '\\') { i += 2; continue; }
      if (source[i++] === '"') return JSON.parse(source.slice(start, i));
    }
    throw new Error('REQUEST_INVALID');
  };
  const value = () => {
    ws();
    if (source[i] === '"') return string();
    if (source[i] === '{') {
      i += 1; ws(); const result = {};
      if (source[i] === '}') { i += 1; return result; }
      while (true) {
        ws(); if (source[i] !== '"') throw new Error('REQUEST_INVALID');
        const key = string(); if (Object.hasOwn(result, key)) throw new Error('REQUEST_INVALID');
        ws(); if (source[i++] !== ':') throw new Error('REQUEST_INVALID');
        Object.defineProperty(result, key, { value: value(), enumerable: true, writable: true, configurable: true }); ws();
        if (source[i] === '}') { i += 1; return result; }
        if (source[i++] !== ',') throw new Error('REQUEST_INVALID');
      }
    }
    if (source[i] === '[') {
      i += 1; ws(); const result = [];
      if (source[i] === ']') { i += 1; return result; }
      while (true) { result.push(value()); ws(); if (source[i] === ']') { i += 1; return result; } if (source[i++] !== ',') throw new Error('REQUEST_INVALID'); }
    }
    const rest = source.slice(i);
    for (const [token, parsed] of [['true', true], ['false', false], ['null', null]]) if (rest.startsWith(token)) { i += token.length; return parsed; }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(rest);
    if (!match) throw new Error('REQUEST_INVALID');
    i += match[0].length; const number = Number(match[0]);
    if (!Number.isFinite(number)) throw new Error('REQUEST_INVALID');
    return number;
  };
  const result = value(); ws(); if (i !== source.length) throw new Error('REQUEST_INVALID'); return result;
}
export async function verifyCandidateProof(sourceRoot, request) {
  if (typeof sourceRoot !== 'string' || !path.isAbsolute(sourceRoot)) throw new Error('REQUEST_INVALID');
  if (!request || Object.keys(request).sort().join(',') !== 'buildIdentity,candidateDirectory,candidateSha256,manifestSha256'
    || typeof request.candidateDirectory !== 'string' || !path.isAbsolute(request.candidateDirectory)
    || typeof request.candidateSha256 !== 'string' || typeof request.manifestSha256 !== 'string'
    || !sha.test(request.candidateSha256) || !sha.test(request.manifestSha256)) throw new Error('REQUEST_INVALID');
  const source = resolveBuildIdentity(sourceRoot);
  if (source.dirty) throw new Error('SOURCE_DIRTY');
  assertBuildIdentityMatches(source, request.buildIdentity);
  const proof = await verifyReleaseCandidate({ candidateDirectory: request.candidateDirectory, candidateSha256: request.candidateSha256 });
  if (proof.manifestSha256 !== request.manifestSha256) throw new Error('MANIFEST_DIGEST_MISMATCH');
  assertBuildIdentityMatches(proof.buildIdentity, source);
  const after = resolveBuildIdentity(sourceRoot);
  if (after.dirty) throw new Error('SOURCE_DIRTY');
  assertBuildIdentityMatches(after, source);
  return { candidateSha256: proof.candidateSha256, manifestSha256: proof.manifestSha256, buildIdentity: proof.buildIdentity };
}

async function main() {
  if (process.argv.length !== 2) throw new Error('REQUEST_INVALID');
  const chunks = []; let size = 0;
  for await (const chunk of process.stdin) { size += chunk.length; if (size > 65536) throw new Error('REQUEST_INVALID'); chunks.push(chunk); }
  const input = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
  let request;
  try { request = parseStrict(input); } catch { throw new Error('REQUEST_INVALID'); }
  const proof = await verifyCandidateProof(root, request);
  process.stdout.write(`${JSON.stringify(proof)}\n`);
}
function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) main().catch(() => { process.stderr.write('CANDIDATE_PROOF_INVALID\n'); process.exitCode = 1; });
