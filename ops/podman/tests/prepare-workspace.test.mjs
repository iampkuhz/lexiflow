import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { cleanupPreparedTree, writePreparedTree } from '../prepare-workspace.mjs';

function fixture() { const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-prepare-')); return { dir, work: path.join(dir, 'work') }; }
test('known partial bytes are safely cleaned after a persisted pending intent', async () => {
  const f = fixture(); fs.mkdirSync(f.work);
  let plan;
  await assert.rejects(writePreparedTree(f.work, [{ path: 'extension/a.js', bytes: Buffer.from('expected content') }], async value => {
    plan = structuredClone(value);
    if (plan.files[0].pending) throw new Error('stop after durable intent');
  }), /durable intent/);
  fs.mkdirSync(path.join(f.work, 'extension'), { recursive: true });
  fs.writeFileSync(path.join(f.work, 'extension/a.js'), 'expected');
  cleanupPreparedTree(f.work, plan);
  assert.deepEqual(fs.readdirSync(f.work), []);
  fs.rmSync(f.dir, { recursive: true, force: true });
});

test('unknown entry, wrong prefix and symlink all preserve workspace', async t => {
  for (const mode of ['unknown', 'prefix', 'symlink']) {
    const f = fixture(); fs.mkdirSync(f.work); let plan;
    await assert.rejects(writePreparedTree(f.work, [{ path: 'a.bin', bytes: Buffer.from('expected') }], async value => { plan = structuredClone(value); if (plan.files[0].pending) throw new Error('stop'); }), /stop/);
    if (mode === 'unknown') fs.writeFileSync(path.join(f.work, 'surprise'), 'opaque');
    else if (mode === 'prefix') fs.writeFileSync(path.join(f.work, 'a.bin'), 'wrong');
    else { fs.unlinkSync(path.join(f.work, 'a.bin')); fs.symlinkSync(process.execPath, path.join(f.work, 'a.bin')); }
    assert.throws(() => cleanupPreparedTree(f.work, plan), /UPGRADE_WORKSPACE_DRIFT/);
    assert.equal(fs.existsSync(f.work), true, `${mode} must be retained`);
    t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  }
});

test('bounded limits reject oversized plan before workspace writes', async () => {
  const f = fixture(); fs.mkdirSync(f.work); let calls = 0;
  await assert.rejects(writePreparedTree(f.work, Array.from({ length: 4097 }, (_, i) => ({ path: `${i}`, bytes: Buffer.alloc(0) })), async () => { calls++; }), /UPGRADE_WORKSPACE_LIMIT/);
  assert.equal(calls, 0); assert.deepEqual(fs.readdirSync(f.work), []); fs.rmSync(f.dir, { recursive: true, force: true });
});
