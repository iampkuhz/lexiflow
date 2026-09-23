import assert from "node:assert/strict";
import test from "node:test";
import { LocalPreferences, MAX_SUPPRESSED_ENTRIES, suppressionKey } from "../dist/preferences.js";
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12,"0")}`;

test("explicit local suppression is serialized across tabs and can be fully restored", async () => {
  let stored;
  let writes = 0;
  const prefs = new LocalPreferences({read:async () => stored, write:async ids => {stored=ids;writes++;}});
  assert.deepEqual(await prefs.execute("read"),{ok:true,entryKeys:[]});
  assert.equal(writes,0);
  await Promise.all([prefs.execute("suppress",id(1),1),prefs.execute("suppress",id(2),1)]);
  assert.deepEqual(stored,[suppressionKey(id(1),1),suppressionKey(id(2),1)]);
  await prefs.execute("suppress",id(1),1);
  assert.equal(stored.length,2);
  assert.deepEqual(await prefs.execute("restore-all"),{ok:true,entryKeys:[]});
  assert.deepEqual(stored,[]);
});

test("no arbitrary data or unbounded collection can be stored", async () => {
  let writes=0;
  const prefs=new LocalPreferences({read:async () => Array.from({length:MAX_SUPPRESSED_ENTRIES},(_,i)=>suppressionKey(id(i),1)),write:async () => {writes++;}});
  assert.deepEqual(await prefs.execute("suppress","a caption"),{ok:false,reason:"invalid-request"});
  assert.deepEqual(await prefs.execute("suppress",id(501),1),{ok:false,reason:"limit"});
  assert.equal(writes,0);
});

test("storage failures do not invent a successful preference update", async () => {
  const prefs=new LocalPreferences({read:async () => [],write:async () => {throw new Error("disk");}});
  assert.deepEqual(await prefs.execute("suppress",id(1),1),{ok:false,reason:"storage"});
  assert.deepEqual(await prefs.execute("read"),{ok:true,entryKeys:[]});
  const corrupt=new LocalPreferences({read:async () => ["private text"],write:async () => assert.fail()});
  assert.deepEqual(await corrupt.execute("read"),{ok:false,reason:"storage"});
});


test("suppression is version-scoped and missing or invalid versions cannot be stored", async () => {
  let stored;
  const prefs = new LocalPreferences({read:async () => stored,write:async keys => {stored=keys;}});
  for (const version of [undefined,0,-1,1.5,Number.MAX_SAFE_INTEGER+1]) {
    assert.deepEqual(await prefs.execute("suppress",id(1),version),{ok:false,reason:"invalid-request"});
  }
  await prefs.execute("suppress",id(1),1);
  assert.ok(stored.includes(suppressionKey(id(1),1)));
  assert.ok(!stored.includes(suppressionKey(id(1),2)));
});
