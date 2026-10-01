import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
const nativeFetch = globalThis.fetch;
let listener;
globalThis.chrome = { runtime: { id: 'extension-id', getURL: path => `chrome-extension://extension-id/${path}`, onMessage: { addListener(value) { listener = value; } } } };
await import('../dist/background.js');
let statusBody = { softwareVersion:'1.2.3',apiContract:'caption-hints.v1',mode:'demo',ready:false,reason:'DEMO_MODE',datasetVersion:null };
let postFetch, holdStatus = false, rejectStatus = false;
const statusCalls = [];
Object.defineProperty(globalThis, 'fetch', { configurable:true, get: () => (url, options) => {
  if (String(url).endsWith('/runtime-status')) {
    statusCalls.push({url:String(url),options});
    if (rejectStatus) return Promise.reject(new TypeError('synthetic redirect rejected'));
    if (holdStatus) return new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('abort','AbortError'))));
    return Promise.resolve({ok:true,json:async()=>statusBody});
  }
  return postFetch(url, options);
}, set: value => { postFetch = value; } });
const source = { lexiconEntryId: "00000000-0000-0000-0000-000000000001", lexiconVersion: 1, senseId: "00000000-0000-0000-0000-000000000002" };
const payload = { captionTopicKey:'topic',trackKey:null,lastRequestedSnapshot:null,currentSnapshot:{captions:[{windowId:null,startMs:null,segments:[{key:'key1',text:'reliable',offsetMs:null,append:true,line:0}]}]}};
const sender = (id, documentId='doc') => ({id:'extension-id',tab:{id},documentId});
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
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
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

test('status gate is bodyless no-store GET; incompatible or invalid status prevents caption POST', async () => {
  let posts = 0;
  globalThis.fetch = async () => { posts += 1; return {ok:true,json:async()=>({processedKeys:['key1'],hints:[]})}; };
  const readCount = statusCalls.length;
  statusBody = {...statusBody,apiContract:'future-contract'};
  assert.deepEqual(await send({type:'caption-hints',requestId:'mismatch',payload},sender(8)),{ok:false,reason:'invalid-response'});
  assert.equal(posts,0);
  statusBody = {...statusBody,apiContract:'caption-hints.v1',softwareVersion:'01.2.3'};
  assert.deepEqual(await send({type:'caption-hints',requestId:'invalid-status',payload},sender(8)),{ok:false,reason:'invalid-response'});
  assert.equal(posts,0);
  assert.ok(statusCalls.length >= readCount + 2);
  const call = statusCalls.at(-1);
  assert.equal(call.options.method,'GET'); assert.equal(call.options.cache,'no-store');
  assert.equal(call.options.redirect,'error'); assert.equal(call.options.credentials,'omit');
  assert.equal(call.options.referrerPolicy,'no-referrer');
  assert.equal(Object.hasOwn(call.options,'body'),false);
  statusBody = {softwareVersion:'1.2.3',apiContract:'caption-hints.v1',mode:'demo',ready:false,reason:'DEMO_MODE',datasetVersion:null};
});

test('runtime status proxy is restricted to the exact extension popup sender', async () => {
  globalThis.fetch = () => assert.fail('caption POST must not occur in status-only calls');
  assert.deepEqual(await send({type:'runtime-status'},{id:'extension-id',url:'chrome-extension://extension-id/popup.html'}),
    {ok:true,status:statusBody});
  assert.deepEqual(await send({type:'runtime-status'},sender(9)),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'runtime-status'},{id:'external',url:'chrome-extension://extension-id/popup.html'}),{ok:false,reason:'invalid-request'});
});

test('cancelling during the status GET prevents the caption POST', async () => {
  let posts=0; globalThis.fetch=async()=>{posts++;return {ok:true,json:async()=>({processedKeys:['key1'],hints:[]})};};
  holdStatus=true;
  const pending=send({type:'caption-hints',requestId:'cancel-during-gate',payload},sender(5));
  await Promise.resolve(); await Promise.resolve();
  await send({type:'cancel-caption-hint',requestId:'cancel-during-gate'},sender(5));
  assert.deepEqual(await pending,{ok:false,reason:'aborted'}); assert.equal(posts,0);
  holdStatus=false;
});

test('a rejected status redirect cannot send captions or expose its target', async () => {
  let posts = 0;
  const start = statusCalls.length;
  globalThis.fetch = async () => { posts++; throw new Error('must not post'); };
  rejectStatus = true;
  try {
    assert.deepEqual(await send({type:'caption-hints',requestId:'redirect-gate',payload},sender(12)),
      {ok:false,reason:'network'});
    assert.equal(posts,0);
    assert.equal(statusCalls.length,start + 1);
    assert.equal(Object.hasOwn(statusCalls.at(-1).options,'body'),false);
  } finally { rejectStatus = false; }
});

test('native fetch refuses 307 and 308 caption redirects without reaching their target', {timeout:10000}, async () => {
  let redirectCode = 307;
  const received = [];
  const server = createServer(async (request,response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    received.push({path:request.url,method:request.method,body});
    if (request.url === '/source') {
      response.writeHead(redirectCode,{Location:'/target'}).end();
    } else {
      response.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({processedKeys:['key1'],hints:[]}));
    }
  });
  try {
    await new Promise((resolve,reject) => {
      server.once('error',reject);
      server.listen(0,'127.0.0.1',resolve);
    });
    const endpoint = `http://127.0.0.1:${server.address().port}/source`;
    for (const code of [307,308]) {
      redirectCode = code;
      const before = received.length;
      globalThis.fetch = (url,options) => {
        assert.match(String(url),/^http:\/\/127\.0\.0\.1:\d+\/.*caption-hints$/);
        assert.equal(options.redirect,'error');
        assert.equal(options.credentials,'omit');
        assert.equal(options.referrerPolicy,'no-referrer');
        // 仅测试传输映射到本轮随机端口，保留产品 fetch 的全部参数。
        return nativeFetch(endpoint,options);
      };
      assert.deepEqual(await send({type:'caption-hints',requestId:`redirect-${code}`,payload},sender(13)),
        {ok:false,reason:'network'});
      assert.deepEqual(received.slice(before),[{path:'/source',method:'POST',body:JSON.stringify(payload)}]);
    }
    assert.equal(received.some(item => item.path === '/target'),false);
  } finally {
    server.closeAllConnections();
    if (server.listening) await new Promise((resolve,reject) => server.close(error => error ? reject(error) : resolve()));
  }
});

test('binds response caption to request and does not accept unbound dictionary text', async () => {
  for (const body of [
    { caption: 'different', state: 'READY', hints: [{ ...source, startKey:'key1',endKey:'key1', startOffset: 0, endOffset: 8, chineseGloss: '可靠的' }] },
    { processedKeys:['key1'], hints: [{ ...source, startKey:'key1',endKey:'key1', startOffset: 0, endOffset: 8, chineseGloss: '可靠的，可信的' }] }
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
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ processedKeys:['key1'],
      hints: [{ ...source, startKey:'key1',endKey:'key1', startOffset: 0, endOffset: 8, chineseGloss: '可靠的' }] }) });
    const result = await send({ type: 'caption-hints', requestId: 'bounded', payload }, sender(1));
    assert.equal(result.ok, true);
    assert.equal(logs[0][1].stage, 'api');
    assert.equal(logs[0][1].outcome, 'READY');
    assert.equal(typeof logs[0][1].elapsedMs, 'number');
    assert.equal(JSON.stringify(logs).includes('reliable'), false);
    assert.equal(JSON.stringify(logs).includes('可靠的'), false);
    assert.equal(JSON.stringify(logs).includes('key1'), false);
  } finally { console.info = info; }
});

test('accepts only bounded fixed-cardinality Server-Timing fields', async () => {
  const body = { processedKeys:['key1'], hints: [{ ...source, startKey:'key1',endKey:'key1', startOffset:0, endOffset:8, chineseGloss:'可靠的' }] };
  globalThis.fetch = async () => ({ ok:true, json:async () => body, headers:{ get:() => 'query;dur=12.5, rules;dur=0.25, api;dur=13, private;dur=42' } });
  const result = await send({type:'caption-hints',requestId:'metrics',payload},sender(1));
  assert.deepEqual(result.timings, {query:12.5,rules:.25,api:13});
  globalThis.fetch = async () => ({ ok:true, json:async () => body, headers:{ get:() => 'query;dur=Infinity, rules;dur=-1, api;dur=60001' } });
  assert.equal((await send({type:'caption-hints',requestId:'metrics',payload},sender(1))).timings, undefined);
  globalThis.fetch = async () => ({ ok:true, json:async () => body, headers:{ get:() => 'query;dur=1, query;dur=2, rules;dur=3, api;dur=4' } });
  assert.deepEqual((await send({type:'caption-hints',requestId:'duplicate-metrics',payload},sender(1))).timings,{rules:3,api:4});
  globalThis.fetch = async () => ({ ok:true, json:async () => body, headers:{ get:() => 'query;dur=1, query;dur=broken, rules;dur=2, api;dur=3' } });
  assert.deepEqual((await send({type:'caption-hints',requestId:'mixed-duplicate-metrics',payload},sender(1))).timings,{rules:2,api:3});
});

test('503 is a fixed backend outcome and never includes the response body', async () => {
  globalThis.fetch = async () => ({ ok:false, status:503, text:async()=>{throw new Error('must not read body');} });
  assert.deepEqual(await send({type:'caption-hints',requestId:'backend',payload},sender(1)),{ok:false,reason:'backend_unavailable'});
});

test('diagnostic console failure cannot change response or request cleanup', async () => {
  const info=console.info;console.info=()=>{throw new Error('diagnostic sink unavailable');};
  try {
    const body={processedKeys:['key1'],hints:[]};
    globalThis.fetch=async()=>({ok:true,json:async()=>body});
    assert.deepEqual(await send({type:'caption-hints',requestId:'console-fault',payload},sender(1)),{ok:true,body});
    await send({type:'cancel-caption-hint',requestId:'console-fault'},sender(1));
  } finally { console.info=info; }
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

test('popup identity can read and restore all but cannot suppress; sender identity is exact', async () => {
  let values = { 'lexiflow.suppressed-entries': [`${source.lexiconEntryId}@1`] };
  chrome.storage={local:{ get:async key => ({[key]:values[key]}),set:async next => {values={...values,...next};} }};
  const popup = { id:'extension-id', url:'chrome-extension://extension-id/popup.html' };
  assert.deepEqual(await send({type:'local-preferences',action:'read'},popup),{ok:true,entryKeys:[`${source.lexiconEntryId}@1`]});
  assert.deepEqual(await send({type:'local-preferences',action:'restore-all'},popup),{ok:true,entryKeys:[]});
  assert.deepEqual(await send({type:'local-preferences',action:'suppress',entryId:source.lexiconEntryId,lexiconVersion:1},popup),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'local-preferences',action:'read'}, { ...popup, tab:{id:3} }),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'local-preferences',action:'read'}, { ...popup, url:'chrome-extension://extension-id/not-popup.html' }),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'local-preferences',action:'restore-all'}, { ...popup, id:'external-id' }),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'local-preferences',action:'read'}, { ...popup, tab:{id:3} }),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'local-preferences',action:'read'}, {}),{ok:false,reason:'invalid-request'});
  assert.deepEqual(await send({type:'local-preferences',action:'read'}, {id:'external-id',tab:{id:9}}),{ok:false,reason:'invalid-request'});
});


test('duplicate timing names without a duration invalidate the dimension in either order',async()=>{
  for (const header of ['query, query;dur=3, api;dur=4', 'query;dur=3, query, api;dur=4',
      'query ;dur=3, query;dur=4, api;dur=4']) {
    globalThis.fetch=async()=>({ok:true,json:async()=>({processedKeys:['key1'],hints:[]}),headers:{get:()=>header}});
    assert.deepEqual((await send({type:'caption-hints',requestId:'duplicates',payload},sender(1))).timings,{api:4});
  }
});
