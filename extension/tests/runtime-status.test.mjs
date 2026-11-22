import assert from "node:assert/strict";
import test from "node:test";
import { parseRuntimeStatus, readRuntimeStatus, RUNTIME_STATUS_URL } from "../build/test-modules/runtime-status.js";

const publishedDataset={releaseId:"release-1",preparationId:"prep-1",ruleId:"rules-1",sqlVersion:"4",sha256:"a".repeat(64)};
const valid = { softwareVersion:"1.2.3", apiContract:"caption-hints.v4", mode:"formal", ready:true, reason:"OK", datasetVersion:4, publishedDataset };
test("accepts exact ready and degraded formal runtime shapes", () => {
  assert.deepEqual(parseRuntimeStatus(valid), valid);
  assert.equal(parseRuntimeStatus({...valid, ready:true, reason:"PREWARM_DEGRADED"}).ready, true);
});
test("rejects malformed identities, values, and inconsistent readiness", () => {
  for (const patch of [
    {softwareVersion:"01.2.3"}, {softwareVersion:"0.0.0"}, {softwareVersion:"1.65536.3"},
    {apiContract:"other"}, {apiContract:"caption-hints.v1"}, {mode:"other"}, {reason:"private exception"}, {datasetVersion:-1},
    {reason:"NO_PUBLISHED_DATA",ready:true}, {reason:"DEMO_MODE",mode:"formal",ready:false},
    {mode:"invalid",ready:true}, {extra:true}, {mode:[]}, {ready:true,datasetVersion:0},
    {reason:"NO_PUBLISHED_DATA",datasetVersion:null,ready:false}, {reason:"SCHEMA_MISMATCH",datasetVersion:3,ready:false},
    {reason:"DEPENDENCY_UNAVAILABLE",datasetVersion:0,ready:false},
    {mode:"invalid",reason:"SCHEMA_MISMATCH",datasetVersion:0,ready:false}
  ]) assert.equal(parseRuntimeStatus({...valid,...patch}), undefined);
});

test("runtime read is bodyless no-store GET and an already cancelled request never fetches", async () => {
  const original = globalThis.fetch; let captured;
  try {
    globalThis.fetch = async (url, options) => { captured = {url,options}; return {ok:true,json:async()=>valid}; };
    assert.deepEqual(await readRuntimeStatus(),{ok:true,status:valid});
    assert.equal(captured.url,RUNTIME_STATUS_URL); assert.equal(captured.options.method,"GET");
    assert.equal(captured.options.cache,"no-store"); assert.equal(Object.hasOwn(captured.options,"body"),false);
    assert.equal(captured.options.redirect,"error");
    assert.equal(captured.options.credentials,"omit");
    assert.equal(captured.options.referrerPolicy,"no-referrer");
    const controller = new AbortController(); controller.abort();
    globalThis.fetch = () => assert.fail("aborted status request must not fetch");
    assert.deepEqual(await readRuntimeStatus(controller.signal),{ok:false,reason:"network"});
  } finally { globalThis.fetch = original; }
});
test("accepts explicit demo and unknown dataset identity without formal readiness", () => {
  assert.deepEqual(parseRuntimeStatus({...valid,mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null,publishedDataset:null}),
    {...valid,mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null,publishedDataset:null});
});

test("initializing status is explicit and cannot claim a published identity", () => {
  assert.equal(parseRuntimeStatus({...valid,ready:false,reason:"INITIALIZING",datasetVersion:null,publishedDataset:null}).reason,"INITIALIZING");
  assert.equal(parseRuntimeStatus({...valid,ready:false,reason:"INITIALIZING",datasetVersion:3,publishedDataset:null}).reason,"INITIALIZING");
  assert.equal(parseRuntimeStatus({...valid,ready:false,reason:"INITIALIZING",datasetVersion:3}),undefined);
  assert.equal(parseRuntimeStatus({...valid,ready:false,reason:"INITIALIZING",datasetVersion:null,publishedDataset}),undefined);
});

test("wire version is exact; software and publication identities are diagnostics", async () => {
  const differentBuild = "3.0.0-SNAPSHOT.20261121.gaabbccd.dirty.001122334455";
  const otherDataset = {...publishedDataset, releaseId:"another-publication", sha256:"b".repeat(64)};
  const changed = {...valid, softwareVersion:differentBuild, publishedDataset:otherDataset};
  assert.deepEqual(parseRuntimeStatus(changed), changed);
  // A standalone development backend has no container publication proof.
  assert.equal(parseRuntimeStatus({...changed, publishedDataset:null}).ready, true);
  const original = globalThis.fetch;
  try {
    for (const apiContract of ["caption-hints.v3", "caption-hints.v5"]) {
      globalThis.fetch = async () => ({ok:true,json:async()=>({...changed,apiContract})});
      assert.deepEqual(await readRuntimeStatus(),{ok:false,reason:"protocol-mismatch",expected:"caption-hints.v4",actual:apiContract});
    }
    const {apiContract: _absent, ...withoutContract} = changed;
    globalThis.fetch = async () => ({ok:true,json:async()=>withoutContract});
    assert.deepEqual(await readRuntimeStatus(),{ok:false,reason:"invalid-response"});
  } finally { globalThis.fetch = original; }
});

test("accepts full snapshot build identities but rejects unresolved or malformed versions", () => {
  for (const softwareVersion of ["2.0.0-SNAPSHOT.20261006.gabc1234", "2.0.0-SNAPSHOT.20261006.gabc1234.dirty.0123456789ab"]) {
    assert.equal(parseRuntimeStatus({ ...valid, softwareVersion }).softwareVersion, softwareVersion);
  }
  for (const softwareVersion of ["2.0.0-SNAPSHOT", "0.0.0-SNAPSHOT.20261006.gabc1234", "2.0.0-SNAPSHOT.20261006.gABC1234", "2.0.0-SNAPSHOT.20261006.gabc1234.dirty."]) {
    assert.equal(parseRuntimeStatus({ ...valid, softwareVersion }), undefined);
  }
});


test("snapshot source dates are calendar-valid and mandatory", () => {
  assert.equal(parseRuntimeStatus({ ...valid, softwareVersion: "2.0.0-SNAPSHOT.20240229.gabc1234" }).softwareVersion,
    "2.0.0-SNAPSHOT.20240229.gabc1234");
  for (const date of ["20260229", "20260431", "20261301", "20260001", "00000101", "2026106"]) {
    assert.equal(parseRuntimeStatus({ ...valid, softwareVersion: `2.0.0-SNAPSHOT.${date}.gabc1234` }), undefined);
  }
  assert.equal(parseRuntimeStatus({ ...valid, softwareVersion: "2.0.0-SNAPSHOT.gabc1234" }), undefined);
});
