import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink, readFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { generationCodeVersion, dataGeneration } from '../generation-version.mjs';
const modulePath = path.resolve('ops/dataset/generation-version.mjs');
async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'lexiflow-generation-'));
  await mkdir(path.join(root, 'ops/dataset'), { recursive: true });
  await mkdir(path.join(root, 'backend/product/lexicon/src'), { recursive: true });
  await writeFile(path.join(root, 'ops/dataset/generation-inputs.json'), JSON.stringify({schemaVersion:1,include:['backend/product/lexicon/src/**']}));
  await writeFile(path.join(root, 'ops/dataset/generation-version.mjs'), await readFile(modulePath));
  await writeFile(path.join(root, 'backend/product/lexicon/src/A.java'), 'class A {}\n');
  return root;
}
test('hash includes code/comment and ignores unrelated output', async () => {
 const root=await fixture(); try { const first=await generationCodeVersion(root); await mkdir(path.join(root,'tmp')); await writeFile(path.join(root,'tmp/out'),'noise'); assert.equal(await generationCodeVersion(root),first); await writeFile(path.join(root,'backend/product/lexicon/src/A.java'),'// comment\nclass A {}\n'); assert.notEqual(await generationCodeVersion(root),first); } finally { await rm(root,{recursive:true,force:true}); }
});
test('add/delete/rename affect hash and symlinks or missing inputs fail', async () => {
 const root=await fixture(); try { const first=await generationCodeVersion(root); const dir=path.join(root,'backend/product/lexicon/src'); const a=path.join(dir,'A.java'), b=path.join(dir,'B.java'), c=path.join(dir,'C.java'); await writeFile(b,'class B {}'); assert.notEqual(await generationCodeVersion(root),first); await rm(b); assert.equal(await generationCodeVersion(root),first); await rm(a); await writeFile(c,'class A {}\n'); assert.notEqual(await generationCodeVersion(root),first); await rm(c); await rm(dir,{recursive:true}); await assert.rejects(generationCodeVersion(root),/ENOENT/); await mkdir(dir,{recursive:true}); await symlink(path.join(dir,'B.java'),a); await assert.rejects(generationCodeVersion(root),/symlink/); } finally { await rm(root,{recursive:true,force:true}); }
});
test('generation binds upstream deterministically',()=>{const h='a'.repeat(64);const a=dataGeneration({codeVersion:h,inputSha256:h,upstream:{commit:'fixed'}});const b=dataGeneration({codeVersion:h,inputSha256:h,upstream:{commit:'other'}});assert.notEqual(a.generationVersion,b.generationVersion);assert.equal(a.generationVersion.length,64);});

test('data import generation is deterministic and independent of the application version', () => {
  const codeVersion = 'a'.repeat(64), inputSha256 = 'b'.repeat(64);
  const original = dataGeneration({ codeVersion, inputSha256, upstream: { commit: 'source', files: { a: 'one', b: 'two' } } });
  const reordered = dataGeneration({ codeVersion, inputSha256, upstream: { files: { b: 'two', a: 'one' }, commit: 'source' },
    softwareVersion: '9.9.9', buildId: 'c'.repeat(64) });
  assert.deepEqual(reordered, original);
  assert.notEqual(dataGeneration({ codeVersion, inputSha256: 'd'.repeat(64), upstream: original.upstream }).generationVersion, original.generationVersion);
  assert.notEqual(dataGeneration({ codeVersion: 'e'.repeat(64), inputSha256, upstream: original.upstream }).generationVersion, original.generationVersion);
});

test('production manifest excludes tests and unrelated API source, includes dependency/config/schema bytes', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'lexiflow-generation-scope-'));
  try {
    const manifest = JSON.parse(await readFile('ops/dataset/generation-inputs.json', 'utf8'));
    await mkdir(path.join(root, 'ops/dataset'), { recursive: true });
    await writeFile(path.join(root, 'ops/dataset/generation-inputs.json'), JSON.stringify(manifest));
    await writeFile(path.join(root, 'ops/dataset/generation-version.mjs'), await readFile(modulePath));
    for (const pattern of manifest.include) {
      const relative = pattern.endsWith('/**') ? `${pattern.slice(0, -3)}/Fixture.java` : pattern;
      const file = path.join(root, relative); await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, 'input\n');
    }
    const initial = await generationCodeVersion(root);
    for (const relative of ['backend/product/api/src/main/java/io/lexiflow/api/hints/Controller.java', 'backend/product/lexicon/src/test/Fixture.java', 'backend/product/adapters/src/test/Fixture.java', 'docs/test.md', 'backend/product/api/build/output.jar', 'ops/release/version.txt', '.git/HEAD']) {
      const file = path.join(root, relative); await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, 'unrelated');
    }
    assert.equal(await generationCodeVersion(root), initial);
    for (const relative of ['infra/postgres/schema.sql', 'backend/product/adapters/gradle.lockfile', 'ops/dataset/ecdict-source.lock.json',
      'backend/product/lexicon/src/main/java/io/lexiflow/lexicon/application/importing/Fixture.java',
      'scripts/environment/ecdict_bundle.py', 'ops/podman/fetch-ecdict.sh', 'ops/podman/source-tools.Containerfile']) {
      const file = path.join(root, relative); await writeFile(file, 'changed'); assert.notEqual(await generationCodeVersion(root), initial); await writeFile(file, 'input\n');
    }
    assert.equal(await generationCodeVersion(root), initial);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('ancestor symlinks and escaped manifest inputs fail closed', async () => {
  const root = await fixture();
  try {
    const original = path.join(root, 'backend/product/lexicon/src');
    const target = path.join(root, 'other'); await mkdir(target); await writeFile(path.join(target,'A.java'), 'code');
    await rm(original, {recursive:true}); await symlink(target, original);
    await assert.rejects(generationCodeVersion(root), /symlink/);
    await writeFile(path.join(root, 'ops/dataset/generation-inputs.json'), JSON.stringify({schemaVersion:1,include:['../outside']}));
    await assert.rejects(generationCodeVersion(root), /invalid generation input path/);
  } finally { await rm(root,{recursive:true,force:true}); }
});
