import assert from 'node:assert/strict';
import test from 'node:test';
import {CaptionStreamCoordinator,COALESCE_MS} from '../dist/stream.js';
import {request,snapshot,segment,response,keyedHint} from './caption-fixtures.mjs';
const flush=async()=>{for(let i=0;i<5;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
class Scheduler {jobs=new Map();id=0;setTimeout(fn){this.jobs.set(++this.id,fn);return this.id;}clearTimeout(id){this.jobs.delete(id);}run(){const jobs=[...this.jobs.values()];this.jobs.clear();jobs.forEach(fn=>fn());}}
const event=(sequence,...segments)=>({sequence,key:`event-${sequence}`,videoTimeMs:sequence*1000,request:request(snapshot(...segments))});
function setup(){const scheduler=new Scheduler(),views=[],calls=[],observations=[];let cancelled=0;
 const coordinator=new CaptionStreamCoordinator((e,id)=>{const d=deferred();calls.push({...d,event:e,id});return{promise:d.promise,cancel:()=>cancelled++};},v=>views.push(v),scheduler,o=>observations.push(o));
 return{scheduler,views,calls,coordinator,observations,get cancelled(){return cancelled;}};}
const finish=async(call,hints=[])=>{call.resolve({ok:true,body:response(call.event.request,hints)});await flush();};
test('coalesces observations and freezes last actually dispatched snapshot',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('a','A')));t.coordinator.submit(event(2,segment('a','A'),segment('b',' reliable')));t.scheduler.run();
 assert.equal(t.calls.length,1);assert.equal(t.calls[0].event.request.lastRequestedSnapshot,null);
 t.coordinator.submit(event(3,segment('a','A'),segment('b',' reliable'),segment('c',' method')));
 assert.equal(t.calls.length,1);assert.equal(t.cancelled,0);await finish(t.calls[0]);t.scheduler.run();
 const req=t.calls[1].event.request;assert.deepEqual(req.lastRequestedSnapshot,t.calls[0].event.request.currentSnapshot);
 assert.deepEqual(req.currentSnapshot.captions[0].segments.map(s=>s.append),[false,false,true]);
});
test('late successful result merges into still visible prefix while suffix waits',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));await finish(t.calls[0],[keyedHint()]);
 assert.equal(t.views.at(-1).state,'waiting');assert.equal(t.views.at(-1).hints[0].chineseGloss,'可靠的');
 t.scheduler.run();await finish(t.calls[1]);assert.equal(t.views.at(-1).hints.length,1);
});
test('retains hints through append and shifts them on prefix removal without a request',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('a','A '),segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0],[keyedHint()]);
 t.coordinator.submit(event(2,segment('s1','reliable')));t.scheduler.run();assert.equal(t.calls.length,1);
 assert.equal(t.views.at(-1).hints[0].startOffset,0);
 t.coordinator.submit(event(3,segment('s1','reliable',false,1)));t.scheduler.run();assert.equal(t.calls.length,1);
});
test('unrelated replacement and clear cancel old work, including identical text after seek',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();t.coordinator.clear(2);
 t.coordinator.submit(event(3,segment('s2','reliable')));t.scheduler.run();await finish(t.calls[0],[keyedHint()]);
 assert.equal(t.cancelled,1);assert.equal(t.views.at(-1).hints.length,0);await finish(t.calls[1],[keyedHint('s2')]);assert.equal(t.views.at(-1).state,'ready');
 t.coordinator.submit(event(4,segment('s3','entirely new')));assert.equal(t.views.at(-1).hints.length,0);
});
test('retries pending keys without pretending a sent request succeeded and stops at three attempts',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));
 for(let i=0;i<3;i++){t.scheduler.run();assert.equal(t.calls[i].event.request.currentSnapshot.captions[0].segments[0].append,true);t.calls[i].resolve({ok:false,reason:'network'});await flush();}
 t.scheduler.run();assert.equal(t.calls.length,3);assert.equal(t.views.at(-1).state,'fallback');
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();
 assert.deepEqual(t.calls[3].event.request.currentSnapshot.captions[0].segments.map(s=>s.append),[true,true]);
});
test('no-hint success acknowledges keys and illegal coverage is not accepted',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0]);assert.equal(t.views.at(-1).state,'no-pending');
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();t.calls[1].resolve({ok:true,body:{processedKeys:[],hints:[]}});await flush();
 assert.equal(t.views.at(-1).state,'fallback');t.scheduler.run();assert.equal(t.calls.length,2);
});
test('retains existing hint during failed suffix request and invalidates a changed word boundary',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0],[keyedHint()]);
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();t.calls[1].resolve({ok:false,reason:'rejected'});await flush();assert.equal(t.views.at(-1).hints.length,1);
 t.coordinator.submit(event(3,segment('s1','reliability')));assert.equal(t.views.at(-1).hints.length,0);
});
test('synchronous transport failure uses bounded asynchronous retry and timing has no text',async()=>{
 const scheduler=new Scheduler(),views=[],observations=[];const c=new CaptionStreamCoordinator(()=>{throw Error('transport');},v=>views.push(v),scheduler,o=>observations.push(o));
 c.submit(event(1,segment('s1','reliable')));scheduler.run();await flush();assert.equal(views.at(-1).state,'fallback');
 assert.equal(JSON.stringify(observations).includes('reliable'),false);assert.equal(COALESCE_MS,16);
});

test('same entry is shown once across incremental replies while all new keys are acknowledged',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0],[keyedHint()]);
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' reliable')));t.scheduler.run();await finish(t.calls[1],[keyedHint('s2',1,9)]);
 assert.equal(t.views.at(-1).hints.length,1);assert.equal(t.views.at(-1).hints[0].startOffset,0);
 t.coordinator.submit(event(3,segment('s1','reliable'),segment('s2',' reliable')));t.scheduler.run();assert.equal(t.calls.length,2);
});
test('same entry duplicated within one reply still yields one display hint',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable'),segment('s2',' reliable')));t.scheduler.run();
 await finish(t.calls[0],[keyedHint(),keyedHint('s2',1,9)]);assert.equal(t.views.at(-1).hints.length,1);assert.equal(t.views.at(-1).state,'ready');
});
