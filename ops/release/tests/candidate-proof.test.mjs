import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { parseStrict, verifyCandidateProof } from '../integrity/candidate-source-proof.mjs';
import { assembled, assembledProofBridge } from './verified-candidate-fixture.mjs';

function request(f) {
  return { candidateDirectory: f.candidateDirectory, candidateSha256: f.input.candidateSha256,
    manifestSha256: f.candidate.manifestSha256, buildIdentity: f.candidate.buildIdentity };
}

test('clean assembled candidate is proven only against matching clean source identity', async () => {
  const f = await assembled('2.0.0-SNAPSHOT');
  try {
    const before = await readFile(`${f.candidateDirectory}/candidate.json`);
    const proof = await verifyCandidateProof(f.root, request(f));
    assert.equal(proof.candidateSha256, f.input.candidateSha256);
    assert.equal(proof.manifestSha256, f.candidate.manifestSha256);
    assert.equal(proof.buildIdentity.dirty, false);
    assert.deepEqual(await readFile(`${f.candidateDirectory}/candidate.json`), before);
  } finally { await f.cleanup(); }
});

test('dirty source, different source input and tampered artifact are rejected', async () => {
  const f = await assembled();
  try {
    const input = request(f);
    await writeFile(`${f.root}/ops/release/version.txt`, '2.0.1\n');
    await assert.rejects(verifyCandidateProof(f.root, input), /SOURCE_DIRTY/);
    const g = await assembled('2.0.1');
    try { await assert.rejects(verifyCandidateProof(g.root, input)); }
    finally { await g.cleanup(); }
  } finally { await f.cleanup(); }

  const tampered = await assembled();
  try {
    await writeFile(`${tampered.candidateDirectory}/payload/database/schema.sql`, 'tampered');
    await assert.rejects(verifyCandidateProof(tampered.root, request(tampered)));
  } finally { await tampered.cleanup(); }
});

test('candidate digest, manifest digest and full identity must match', async () => {
  const f = await assembled();
  try {
    const input = request(f);
    await assert.rejects(verifyCandidateProof(f.root, { ...input, candidateSha256: '0'.repeat(64) }));
    await assert.rejects(verifyCandidateProof(f.root, { ...input, manifestSha256: '0'.repeat(64) }));
    await assert.rejects(verifyCandidateProof(f.root, { ...input, buildIdentity: { ...input.buildIdentity,
      sourceSha256: '0'.repeat(64) } }));
    const before = await readFile(`${f.candidateDirectory}/candidate.json`);
    await writeFile(`${f.candidateDirectory}/candidate.json`, Buffer.concat([before, Buffer.from(' ')]));
    await assert.rejects(verifyCandidateProof(f.root, input));
  } finally { await f.cleanup(); }
});

test('CLI bridge rejects caller-shaped requests without producing proof output', () => {
  // The process entrypoint is covered by the consumer integration contract; this
  // library test deliberately never supplies sourceRoot through production stdin.
  assert.equal(typeof verifyCandidateProof, 'function');
  assert.throws(() => parseStrict('{"candidateSha256":"a","candidateSha256":"b"}'));
  assert.throws(() => parseStrict('{"value":NaN}'));
  for (const space of ['\u00a0', '\ufeff', '\v', '\f']) assert.throws(() => parseStrict(`${space}{}`));
  assert.deepEqual(parseStrict(' \t\r\n{}\n'), {});
  assert.deepEqual(Object.keys(parseStrict('{"__proto__":1}')), ['__proto__']);
  assert.throws(() => parseStrict('{"__proto__":1,"__proto__":2}'));
  const cli = fileURLToPath(new URL('../integrity/candidate-source-proof.mjs', import.meta.url));
  for (const [payload, args, expected] of [
    ['{}', [], 1],
    ['{}', ['extra'], 1],
    [Buffer.from([0xc3, 0x28]), [], 1],
  ]) {
    const result = spawnSync(process.execPath, [cli, ...args], { input: payload });
    assert.equal(result.status, expected);
    assert.equal(result.stdout.length, 0);
  }
});

test('real Python candidate consumer crosses bounded process boundary to real fixture Node CLI', async () => {
  const f = await assembledProofBridge('2.0.0-SNAPSHOT');
  try {
    const python = String.raw`import json, sys
from pathlib import Path
from unittest.mock import patch
import scripts.acceptance.evidence.verify_candidate_acceptance_evidence as consumer
root, candidate, target_json = sys.argv[1:]
target = json.loads(target_json)
consumer.__file__ = str(Path(root) / 'ops/release/integrity/consumer-anchor.py')
proof = {'target': target}
with patch.object(consumer, '_extract', return_value=(proof, b'{}', {})):
    try:
        value, _, _ = consumer._prove_candidate(Path(root), {}, Path(candidate))
        print(json.dumps({'result': 'PASS', 'node': value['target']}))
    except consumer.CandidateProofError as error:
        print(json.dumps({'result': 'REJECTED', 'reason': str(error)}))
`;
    const invoke = (target) => spawnSync('python3', ['-c', python,
      f.root, f.candidateDirectory, JSON.stringify(target)], {
      encoding: 'utf8', env: { ...process.env, PYTHONPATH: repoRootForTest() }, timeout: 65000
    });
    const correct = { candidateSha256: f.input.candidateSha256,
      manifestSha256: f.candidate.manifestSha256, buildIdentity: f.candidate.buildIdentity };
    const positive = invoke(correct);
    assert.equal(positive.status, 0, `exit=${positive.status}\nstdout=${positive.stdout}\nstderr=${positive.stderr}`);
    assert.equal(JSON.parse(positive.stdout).result, 'PASS', positive.stdout);
    for (const mutated of [
      { candidateSha256: '0'.repeat(64), manifestSha256: correct.manifestSha256,
        buildIdentity: correct.buildIdentity },
      { ...correct, manifestSha256: '0'.repeat(64) },
      { ...correct, buildIdentity: { ...correct.buildIdentity, sourceSha256: '0'.repeat(64) } },
    ]) {
      const negative = invoke(mutated);
      assert.equal(negative.status, 0, `exit=${negative.status}\nstdout=${negative.stdout}\nstderr=${negative.stderr}`);
      assert.equal(JSON.parse(negative.stdout).result, 'REJECTED', negative.stdout);
    }
  } finally { await f.cleanup(); }
});

function repoRootForTest() { return path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..'); }
