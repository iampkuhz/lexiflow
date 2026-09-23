import assert from 'node:assert/strict';
import test from 'node:test';
let listener;
globalThis.chrome = { runtime: { onMessage: { addListener(value) { listener = value; } } } };
await import('../dist/background.js');
const source = { lexiconEntryId: "00000000-0000-0000-0000-000000000001", lexiconVersion: 1, senseId: "00000000-0000-0000-0000-000000000002" };
const payload = { contentId:'00000000-0000-5000-8000-000000000001',contentRevision:1,segmentId:'a'.repeat(64),caption:'reliable',startOffset:0,endOffset:8 };
const sender = (id, documentId='doc') => ({tab:{id},documentId});
const send = (message, source) => new Promise(resolve => listener(message,source,resolve));

test('isolates cancellation between tabs and documents with identical local sequence IDs', async () => {
  const requests=[];
  globalThis.fetch = (_url, options) => new Promise((_resolve,reject) => {
    requests.push(options.signal);
    options.signal.addEventListener('abort',()=>reject(new DOMException('abort','AbortError')));
  });
  const message={type:'caption-hints',requestId:'caption-1-1',payload};
  const first=send(message,sender(1));
  const second=send(message,sender(2));
  const nextDocument=send(message,sender(1,'next'));
  await send({type:'cancel-caption-hint',requestId:message.requestId},sender(1));
  assert.equal(requests[0].aborted,true);
  assert.equal(requests[1].aborted,false);
  assert.equal(requests[2].aborted,false);
  await send({type:'cancel-caption-hint',requestId:message.requestId},sender(2));
  await send({type:'cancel-caption-hint',requestId:message.requestId},sender(1,'next'));
  assert.deepEqual(await Promise.all([first,second,nextDocument]),Array(3).fill({ok:false,reason:'aborted'}));
});

test('rejects malformed request IDs and missing tab identity without fetching', async () => {
  globalThis.fetch=()=>assert.fail('must not fetch');
  for(const requestId of [undefined,null,17,'','x'.repeat(129)]) {
    assert.deepEqual(await send({type:'caption-hints',requestId,payload},sender(1)),{ok:false,reason:'invalid-request'});
  }
  assert.deepEqual(await send({type:'caption-hints',requestId:'x',payload},{}),{ok:false,reason:'invalid-request'});
});

test('binds response caption to request and does not accept unbound dictionary text', async () => {
  for (const body of [
    { caption: 'different', state: 'READY', hints: [{ ...source, startOffset: 0, endOffset: 8, chineseGloss: '可靠的' }] },
    { caption: payload.caption, state: 'READY', hints: [{ ...source, startOffset: 0, endOffset: 8, chineseGloss: '可靠的，可信的' }] }
  ]) {
    globalThis.fetch = async () => ({ ok: true, json: async () => body });
    assert.deepEqual(await send({ type: 'caption-hints', requestId: 'bounded', payload }, sender(1)), { ok: false, reason: 'invalid-response' });
  }
});

test('diagnostic logs expose stage outcome and duration but not input or model output', async () => {
  const logs = [];
  const info = console.info;
  console.info = (...values) => logs.push(values);
  try {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ caption: payload.caption, state: 'READY',
      hints: [{ ...source, startOffset: 0, endOffset: 8, chineseGloss: '可靠的' }] }) });
    const result = await send({ type: 'caption-hints', requestId: 'bounded', payload }, sender(1));
    assert.equal(result.ok, true);
    assert.equal(logs[0][1].stage, 'api');
    assert.equal(logs[0][1].outcome, 'READY');
    assert.equal(typeof logs[0][1].elapsedMs, 'number');
    assert.equal(JSON.stringify(logs).includes(payload.caption), false);
    assert.equal(JSON.stringify(logs).includes('可靠的'), false);
    assert.equal(JSON.stringify(logs).includes(payload.segmentId), false);
  } finally { console.info = info; }
});

test('accepts only bounded fixed-cardinality Server-Timing fields', async () => {
  const body = { caption: payload.caption, state: 'READY', hints: [{ ...source, startOffset:0, endOffset:8, chineseGloss:'可靠的' }] };
  globalThis.fetch = async () => ({ ok:true, json:async () => body, headers:{ get:() => 'query;dur=12.5, rules;dur=0.25, api;dur=13, private;dur=42' } });
  const result = await send({type:'caption-hints',requestId:'metrics',payload},sender(1));
  assert.deepEqual(result.timings, {query:12.5,rules:.25,api:13});
  globalThis.fetch = async () => ({ ok:true, json:async () => body, headers:{ get:() => 'query;dur=Infinity, rules;dur=-1, api;dur=60001' } });
  assert.equal((await send({type:'caption-hints',requestId:'metrics',payload},sender(1))).timings, undefined);
});

test('timeout is distinct from explicit cancellation', async () => {
  const schedule = globalThis.setTimeout;
  const cancel = globalThis.clearTimeout;
  let deadline;
  globalThis.setTimeout = fn => { deadline = fn; return 1; };
  globalThis.clearTimeout = () => {};
  try {
    globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
    const request = send({type:'caption-hints',requestId:'deadline',payload},sender(1));
    deadline();
    assert.deepEqual(await request,{ok:false,reason:'timeout'});
  } finally { globalThis.setTimeout=schedule; globalThis.clearTimeout=cancel; }
});

test('malformed JSON is an invalid response rather than a network failure', async () => {
  globalThis.fetch = async () => ({ ok:true, json:async () => { throw new SyntaxError('invalid JSON'); } });
  assert.deepEqual(await send({type:'caption-hints',requestId:'json',payload},sender(1)),{ok:false,reason:'invalid-response'});
});

test('preferences only write explicit entry IDs to local storage and never fetch', async () => {
  let values={};
  chrome.storage={local:{ get:async key => ({[key]:values[key]}),set:async next => {values={...values,...next};} }};
  globalThis.fetch=()=>assert.fail('preference action must not reach the server');
  const entryId=source.lexiconEntryId;
  assert.deepEqual(await send({type:'local-preferences',action:'suppress',entryId,lexiconVersion:1},sender(1)),{ok:true,entryKeys:[`${entryId}@1`]});
  assert.deepEqual(values,{'lexiflow.suppressed-entries':[`${entryId}@1`]});
  assert.deepEqual(await send({type:'local-preferences',action:'read'},sender(2)),{ok:true,entryKeys:[`${entryId}@1`]});
  assert.deepEqual(await send({type:'local-preferences',action:'restore-all'},sender(1)),{ok:true,entryKeys:[]});
  assert.deepEqual(await send({type:'local-preferences',action:'suppress',entryId,lexiconVersion:1},{}),{ok:false,reason:'invalid-request'});
});
