import assert from 'node:assert/strict';
import {test} from 'node:test';
import {cp, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import { encodeLock, parseLock, readLock } from '../../scripts/contracts/contract-lock.mjs';
import { extensionContract } from '../../scripts/contracts/extension-contract.mjs';
import { nextRecord } from '../../scripts/contracts/version-contract.mjs';

test('only the two canonical version records are accepted within the byte budget', () => {
  const lock = readLock();
  assert.deepEqual(parseLock(encodeLock(lock)), lock);
  for (const bad of [encodeLock(lock).repeat(100), encodeLock(lock).replace(/"version": \d+/u, '"version": 0'),
    encodeLock(lock).replace(/"version": \d+/u, '"version": 4, "version": 4'),
    encodeLock(lock).replace('"sqlite-storage"', '"unknown"')])
    assert.throws(() => parseLock(bad));
});

test('repeated changes against one base increment once; comparison follows the selected target', () => {
  const a = 'a'.repeat(64), b = 'b'.repeat(64), c = 'c'.repeat(64);
  const base = {version:4,sha256:a};
  assert.deepEqual(nextRecord(base,a), base);
  assert.deepEqual(nextRecord(base,b), {version:5,sha256:b});
  assert.deepEqual(nextRecord(base,c), {version:5,sha256:c});
  assert.deepEqual(nextRecord(nextRecord(base,b),c), {version:6,sha256:c});
});

test('projection separates layout, advisory open replies, strict fields, and declared meaning', async t => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const fixture = await mkdtemp(join(tmpdir(),'lexiflow-wire-'));
  t.after(()=>rm(fixture,{recursive:true,force:true}));
  await mkdir(join(fixture,'backend/product/api/src/main'),{recursive:true});
  for (const relative of ['extension/src','backend/product/api/src/main','backend/product/enrichment/src/main'])
    await cp(join(root,relative),join(fixture,relative),{recursive:true});
  const baseline = extensionContract(fixture);
  const changed = async (name, search, value, check) => {
    const path = join(fixture,name), before = await readFile(path,'utf8');
    assert.ok(before.includes(search), search);
    await writeFile(path,before.replace(search,value));
    try { check(extensionContract(fixture)); } finally { await writeFile(path,before); }
  };
  const api = 'backend/product/api/src/main/java/io/lexiflow/api/';
  await changed('extension/src/manage.html','LexiFlow','LexiFlow UI',result=>assert.deepEqual(result,baseline));
  await changed('extension/src/backend-target.ts','18080','18081',result=>assert.deepEqual(result,baseline));
  await changed(api+'preferences/LexiconBrowseController.java','long total, int page, int pageSize)',
    'long total, int page, int pageSize, @WireAdvisory String label)',result=>assert.deepEqual(result,baseline));
  await changed(api+'runtime/RuntimeStatus.java','PublishedDataset publishedDataset)',
    'PublishedDataset publishedDataset, String label)',result=>assert.notDeepEqual(result,baseline));
  await changed(api+'hints/CaptionHintController.java','utf16-code-unit','unicode-code-point',result=>assert.notDeepEqual(result,baseline));
});
