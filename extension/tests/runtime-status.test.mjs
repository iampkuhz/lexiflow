import assert from "node:assert/strict";
import test from "node:test";
import { parseRuntimeStatus, readRuntimeStatus, RUNTIME_STATUS_URL } from "../dist/runtime-status.js";

const valid = { softwareVersion:"1.2.3", apiContract:"caption-hints.v1", mode:"formal", ready:true, reason:"OK", datasetVersion:4 };
test("accepts exact ready and degraded formal runtime shapes", () => {
  assert.deepEqual(parseRuntimeStatus(valid), valid);
  assert.equal(parseRuntimeStatus({...valid, ready:true, reason:"PREWARM_DEGRADED"}).ready, true);
});
test("rejects malformed identities, values, and inconsistent readiness", () => {
  for (const patch of [
    {softwareVersion:"01.2.3"}, {softwareVersion:"0.0.0"}, {softwareVersion:"1.65536.3"},
    {apiContract:"other"}, {mode:"other"}, {reason:"private exception"}, {datasetVersion:-1},
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
  assert.deepEqual(parseRuntimeStatus({...valid,mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null}),
    {...valid,mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null});
});
