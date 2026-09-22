import assert from 'node:assert/strict';
import test from 'node:test';
let listener;
globalThis.chrome = { runtime: { onMessage: { addListener(value) { listener = value; } } } };
await import('../dist/background.js');
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
