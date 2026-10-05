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
test('coalesces observations and acknowledges only the successful dispatched snapshot',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('a','A')));t.coordinator.submit(event(2,segment('a','A'),segment('b',' reliable')));t.scheduler.run();
 assert.equal(t.calls.length,1);assert.equal(t.calls[0].event.request.lastRequestedSnapshot,null);
 t.coordinator.submit(event(3,segment('a','A'),segment('b',' reliable'),segment('c',' method')));
 assert.equal(t.calls.length,1);assert.equal(t.cancelled,0);await finish(t.calls[0]);t.scheduler.run();
 const req=t.calls[1].event.request;assert.deepEqual(req.lastRequestedSnapshot,t.calls[0].event.request.currentSnapshot);
 assert.deepEqual(req.currentSnapshot.captions[0].segments.map(s=>s.append),[false,false,true]);
});
test('separates cancelled scheduled sends, in-flight cancellation, and late completion',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('a','A')));t.coordinator.submit(event(2,segment('b','B')));
 assert.equal(t.observations.filter(o=>o.outcome==='cancelled_before_send').length,1);
 t.scheduler.run();t.coordinator.clear(3);assert.equal(t.observations.filter(o=>o.outcome==='cancelled_in_flight').length,1);
 await finish(t.calls[0]);assert.equal(t.observations.filter(o=>o.outcome==='late_response').length,1);
});
test('no-hint is based on current response rather than retained hints and observation faults are isolated',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('a','reliable')));t.scheduler.run();await finish(t.calls[0],[keyedHint('a')]);
 const second=event(2,segment('a','reliable'),segment('b',' method'));
 t.coordinator.submit(second);t.scheduler.run();await finish(t.calls[1]);
 assert.equal(t.views.at(-1).hints.length,1);assert.equal(t.observations.filter(o=>o.outcome==='no-pending').length,0);
 assert.equal(t.observations.filter(o=>o.outcome==='ready').length,2);
 assert.equal(t.observations.filter(o=>o.outcome==='no_hint').length,1);
});
test('late successful result merges into still visible prefix while suffix waits',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));await finish(t.calls[0],[keyedHint()]);
 assert.equal(t.views.at(-1).state,'waiting');assert.equal(t.views.at(-1).hints[0].chineseGloss,'可靠的');
 t.scheduler.run();await finish(t.calls[1]);assert.equal(t.views.at(-1).hints.length,1);
});
test('a row is frozen when a newer visual row arrives, so a late reply cannot add Chinese to it',async()=>{
 const t=setup();
 t.coordinator.submit(event(1,segment('a','An old ',true,0),segment('b','reliable',true,1)));
 t.scheduler.run();
 t.coordinator.submit(event(2,segment('b','reliable',false,0),segment('c',' new words',true,1)));
 await finish(t.calls[0],[keyedHint('b')]);
 assert.equal(t.views.at(-1).hints.length,0);
 t.scheduler.run();await finish(t.calls[1]);
 assert.equal(t.views.at(-1).hints.length,0);
});
test('an already shown hint keeps its identity and gloss when its row becomes old',async()=>{
 const t=setup();
 t.coordinator.submit(event(1,segment('a','An old ',true,0),segment('b','reliable',true,1)));
 t.scheduler.run();await finish(t.calls[0],[keyedHint('b')]);
 t.coordinator.submit(event(2,segment('b','reliable',false,0),segment('c',' new words',true,1)));
 assert.deepEqual(t.views.at(-1).hints.map(hint=>hint.chineseGloss),['可靠的']);
 t.scheduler.run();await finish(t.calls[1]);
 assert.deepEqual(t.views.at(-1).hints.map(hint=>hint.chineseGloss),['可靠的']);
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
for (const reason of ['network', 'timeout', 'rejected', 'invalid-request', 'invalid-response', 'aborted']) {
 test(`first ${reason} failure stays idle until the next caption and preserves null baseline`,async()=>{
  const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();
  t.calls[0].resolve({ok:false,reason});await flush();
  assert.equal(t.scheduler.jobs.size,0);t.scheduler.run();assert.equal(t.calls.length,1);
  assert.equal(t.views.at(-1).state,'fallback');
  if(reason==='invalid-response') assert.equal(t.observations.filter(o=>o.outcome==='protocol_mismatch').length,1);
  t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();
  const req=t.calls[1].event.request;
  assert.equal(req.lastRequestedSnapshot,null);
  assert.deepEqual(req.currentSnapshot.captions[0].segments.map(s=>s.append),[true,true]);
  await finish(t.calls[1]);assert.equal(t.views.at(-1).state,'no-pending');
 });
}
test('failed suffix preserves successful baseline and resends only unacknowledged visible keys',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();
 await finish(t.calls[0],[keyedHint()]);
 const baseline=structuredClone(t.calls[0].event.request.currentSnapshot);
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();
 t.calls[1].resolve({ok:false,reason:'network'});await flush();
 assert.equal(t.views.at(-1).hints.length,1);assert.equal(t.scheduler.jobs.size,0);
 t.coordinator.submit(event(3,segment('s1','reliable'),segment('s2',' method'),segment('s3',' works')));t.scheduler.run();
 assert.deepEqual(t.calls[2].event.request.lastRequestedSnapshot,baseline);
 assert.deepEqual(t.calls[2].event.request.currentSnapshot.captions[0].segments.map(s=>s.append),[false,true,true]);
 await finish(t.calls[2]);
 t.coordinator.submit(event(4,segment('s1','reliable'),segment('s2',' method'),segment('s3',' works'),segment('s4',' well')));t.scheduler.run();
 assert.deepEqual(t.calls[3].event.request.lastRequestedSnapshot,t.calls[2].event.request.currentSnapshot);
 assert.deepEqual(t.calls[3].event.request.currentSnapshot.captions[0].segments.map(s=>s.append),[false,false,false,true]);
});
test('a failed in-flight request does not drain newer observations until a subsequent change',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));
 t.calls[0].resolve({ok:false,reason:'network'});await flush();t.scheduler.run();
 assert.equal(t.calls.length,1);assert.equal(t.scheduler.jobs.size,0);
 t.coordinator.submit(event(3,segment('s2',' method'),segment('s3',' works')));t.scheduler.run();
 assert.equal(t.calls[1].event.request.lastRequestedSnapshot,null);
 assert.deepEqual(t.calls[1].event.request.currentSnapshot.captions[0].segments.map(s=>s.key),['s2','s3']);
 assert.deepEqual(t.calls[1].event.request.currentSnapshot.captions[0].segments.map(s=>s.append),[true,true]);
});
test('invalid successful response cannot advance the acknowledged snapshot',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0]);
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();
 t.calls[1].resolve({ok:true,body:{processedKeys:[],hints:[]}});await flush();
 assert.equal(t.scheduler.jobs.size,0);
 t.coordinator.submit(event(3,segment('s1','reliable'),segment('s2',' method'),segment('s3',' works')));t.scheduler.run();
 assert.deepEqual(t.calls[2].event.request.lastRequestedSnapshot,t.calls[0].event.request.currentSnapshot);
 assert.deepEqual(t.calls[2].event.request.currentSnapshot.captions[0].segments.map(s=>s.append),[false,true,true]);
});
test('source reset discards the baseline and late success cannot restore it',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0]);
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' method')));t.scheduler.run();
 t.coordinator.clear(3);await finish(t.calls[1]);
 t.coordinator.submit(event(4,segment('s3','reliable')));t.scheduler.run();
 assert.equal(t.calls[2].event.request.lastRequestedSnapshot,null);
});
test('temporary unknown JSON3 track keeps segment identity and confirmed coverage',async()=>{
 const t=setup();
 const tracked=(sequence,trackKey,segments)=>({...event(sequence,...segments),request:{...request(snapshot(...segments)),trackKey}});
 t.coordinator.submit(tracked(1,'en:asr',[segment('old','A reliable')]));t.scheduler.run();await finish(t.calls[0],[keyedHint('old',2,10)]);
 const baseline=t.calls[0].event.request.currentSnapshot;
 t.coordinator.submit(tracked(2,null,[segment('old','A reliable',false)]));t.scheduler.run();
 assert.equal(t.calls.length,1);assert.equal(t.views.at(-1).hints.length,1);
 t.coordinator.submit(tracked(3,null,[segment('old','A reliable',false),segment('new',' method')]));t.scheduler.run();
 assert.equal(t.calls[1].event.request.trackKey,null);
 assert.deepEqual(t.calls[1].event.request.lastRequestedSnapshot,baseline);
 assert.deepEqual(t.calls[1].event.request.currentSnapshot.captions[0].segments.map(s=>[s.key,s.append]),[['old',false],['new',true]]);
 await finish(t.calls[1]);
 t.coordinator.submit(tracked(4,'en:asr',[segment('old','A reliable',false),segment('new',' method',false)]));t.scheduler.run();
 assert.equal(t.calls.length,2);assert.equal(t.views.at(-1).hints.length,1);
 t.coordinator.submit(tracked(5,'en:other',[segment('old','A reliable',false),segment('new',' method',false),segment('later',' again')]));t.scheduler.run();
 assert.equal(t.calls[2].event.request.lastRequestedSnapshot,null);
 assert.deepEqual(t.calls[2].event.request.currentSnapshot.captions[0].segments.map(s=>s.append),[true,true,true]);
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
test('synchronous transport failure waits for a caption change and timing has no text',async()=>{
 const scheduler=new Scheduler(),views=[],observations=[];let calls=0;const c=new CaptionStreamCoordinator(()=>{calls++;throw Error('transport');},v=>views.push(v),scheduler,o=>observations.push(o));
 c.submit(event(1,segment('s1','reliable')));scheduler.run();await flush();assert.equal(views.at(-1).state,'fallback');
 assert.equal(scheduler.jobs.size,0);scheduler.run();assert.equal(calls,1);
 c.submit(event(2,segment('s1','reliable'),segment('s2',' method')));scheduler.run();await flush();assert.equal(calls,2);
 assert.equal(scheduler.jobs.size,0);assert.equal(JSON.stringify(observations).includes('reliable'),false);assert.equal(COALESCE_MS,16);
});

test('same entry at a new non-overlapping range is preserved across incremental replies',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();await finish(t.calls[0],[keyedHint()]);
 t.coordinator.submit(event(2,segment('s1','reliable'),segment('s2',' reliable')));t.scheduler.run();await finish(t.calls[1],[keyedHint('s2',1,9)]);
 assert.equal(t.views.at(-1).hints.length,2);assert.deepEqual(t.views.at(-1).hints.map(hint=>hint.startOffset),[0,9]);
 t.coordinator.submit(event(3,segment('s1','reliable'),segment('s2',' reliable')));t.scheduler.run();assert.equal(t.calls.length,2);
});
test('same entry at distinct non-overlapping ranges is preserved within one reply',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable'),segment('s2',' reliable')));t.scheduler.run();
 await finish(t.calls[0],[keyedHint(),keyedHint('s2',1,9)]);assert.equal(t.views.at(-1).hints.length,2);assert.equal(t.views.at(-1).state,'ready');
});
test('partial Server-Timing records measured dimensions and one missing event per accepted response',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable')));t.scheduler.run();
 t.calls[0].resolve({ok:true,body:response(t.calls[0].event.request,[]),timings:{query:2,api:5}});await flush();
 assert.deepEqual(t.observations.filter(o=>o.stage==='query').map(o=>o.elapsedMs),[2]);
 assert.equal(t.observations.filter(o=>o.stage==='rules').length,0);
 assert.equal(t.observations.filter(o=>o.outcome==='missing-server-timing').length,1);
 assert.equal(t.observations.filter(o=>o.outcome==='no_hint').length,1);
});
test('same lexicon entry may occur again in a non-overlapping caption interval', async () => {
 const t=setup();t.coordinator.submit(event(1,segment('s1','reliable and reliable')));t.scheduler.run();
 const first=keyedHint('s1',0,8), second=keyedHint('s1',13,21);
 await finish(t.calls[0],[first,second]);
 assert.equal(t.views.at(-1).hints.length,2);
 assert.deepEqual(t.views.at(-1).hints.map(h=>[h.startOffset,h.endOffset]),[[0,8],[13,21]]);
});

test('accepted no-hint response retains a bounded event receipt for visible English logging',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','ordinary English')));t.scheduler.run();
 const requestId='00000000-0000-0000-0000-000000000123';
 t.calls[0].resolve({ok:true,requestId,body:response(t.calls[0].event.request,[])});await flush();
 assert.equal(t.views.at(-1).state,'no-pending');
 assert.equal(t.views.at(-1).debugRequestId,requestId);
 assert.equal(t.views.at(-1).debugRequestEvent.sequence,1);
});
test('invalid or absent request ID never becomes a fabricated request correlation ID',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('s1','untranslated')));t.scheduler.run();
 t.calls[0].resolve({ok:true,body:response(t.calls[0].event.request,[])});await flush();
 assert.equal(t.views.at(-1).debugRequestId,undefined);
 assert.equal(t.views.at(-1).debugRequestEvent.sequence,1);
});
test('natural same-track line replacement exposes finalized keys but clear/reset never does',async()=>{
 const t=setup();t.coordinator.submit(event(1,segment('old-line','first sentence')));t.scheduler.run();await finish(t.calls[0]);
 t.coordinator.submit(event(2,segment('new-line','completely different')));
 assert.ok(t.views.at(-1).finalizedKeys.includes('old-line'));
 t.coordinator.clear(3);assert.deepEqual(t.views.at(-1).finalizedKeys ?? [],[]);
});
