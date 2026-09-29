import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { CaptionCapture } from '../dist/caption-capture.js';
import { createPageLifecycle } from '../dist/page-lifecycle.js';

const original={};for(const key of ['window','document','MutationObserver'])original[key]=globalThis[key];
class Target extends EventTarget{hidden=false;documentElement={};}
class Observer{constructor(callback){this.callback=callback;}observe(){}disconnect(){}}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
function fixture(){
  const doc=new Target(),win=new Target();let videoId='A',caption='',gap=false,hashes=[],hashCalls=0,pageKey=0;
  const video={ended:false,currentTime:1};
  const segment={get textContent(){return caption;},get innerText(){return caption;},checkVisibility:()=>!!caption,
    closest:()=>null,getBoundingClientRect:()=>({top:0})};
  const nativeContainer={checkVisibility:()=>gap||!!caption};
  const player={classList:{contains:()=>false},querySelector(selector){return selector==='video'?video:selector==='#ytp-caption-window-container'?nativeContainer:null;},
    querySelectorAll:selector=>selector==='.ytp-caption-segment'&&caption?[segment]:[]};
  doc.querySelector=selector=>selector==='.html5-video-player, #movie_player'?player:
    selector==='.html5-video-player video, #movie_player video'?video:null;
  doc.getElementById=()=>({});
  globalThis.document=doc;globalThis.window=win;globalThis.MutationObserver=Observer;
  const submissions=[],clears=[];let capture;
  const lifecycle=createPageLifecycle({currentVideoId:()=>videoId,hasPlayer:()=>true,hasCaptionMotion:()=>false},
    ()=>capture?.invalidate(),()=>{void capture?.capture();},()=>{},()=>`page-${++pageKey}`);
  capture=new CaptionCapture(lifecycle,{submit:event=>submissions.push(event),clear:sequence=>clears.push(sequence)},
    {sha256:value=>{hashCalls++;const next=hashes.shift();assert.ok(next,`unexpected topic hash for ${value}`);return next.promise;},
      now:()=>0,onWaiting:()=>{},onSource:()=>{},onObserved:()=>{},onOversized:()=>{},onAcquisition:()=>{}});
  return {doc,win,video,player,lifecycle,capture,submissions,clears,hashes,
    setGap:value=>{gap=value;},setCaption:value=>{caption=value;},setVideo:value=>{videoId=value;},get hashCalls(){return hashCalls;},
    nextHash(){const value=deferred();hashes.push(value);return value;}};
}
async function flush(){for(let i=0;i<6;i++)await Promise.resolve();}
afterEach(()=>{for(const[key,value]of Object.entries(original))globalThis[key]=value;});
function snapshotText(event){return event.request.currentSnapshot.captions.flatMap(group=>group.segments).map(segment=>segment.text).join('');}

test('actual capture shares same-page hash but only current source snapshot is submitted',async()=>{
  const f=fixture(),hash=f.nextHash();f.setCaption('A reliable');
  const first=f.capture.capture();await flush();f.setCaption('A reliable method');
  const second=f.capture.capture();await flush();assert.equal(f.hashCalls,1);assert.equal(f.submissions.length,0);
  hash.resolve('topic-A');await Promise.all([first,second]);
  assert.equal(f.submissions.length,1);assert.equal(f.submissions[0].request.captionTopicKey,'topic-A');
  assert.equal(snapshotText(f.submissions[0]),'A reliable method');
  f.setCaption('A reliable method now');await f.capture.capture();
  assert.equal(f.hashCalls,1,'resolved topic is cached only inside the current page');
  assert.equal(f.submissions.length,2);assert.equal(snapshotText(f.submissions[1]),'A reliable method now');
});

test('actual capture rejects out-of-order A to B to A success and an old rejection cannot clear the new page hash',async()=>{
  const f=fixture(),oldA=f.nextHash();f.setCaption('Alpha source');const operationA=f.capture.capture();await flush();
  f.setVideo('B');f.lifecycle.refreshPage();const oldB=f.nextHash();f.setCaption('Bravo source');const operationB=f.capture.capture();await flush();
  f.setVideo('A');f.lifecycle.refreshPage();const currentA=f.nextHash();f.setCaption('Alpha current');const operationCurrent=f.capture.capture();await flush();
  oldA.resolve('topic-old-A');await operationA;assert.equal(f.submissions.length,0);
  // Rejection from the superseded A generation must not clear the B generation's cached promise.
  oldB.reject(new Error('old B failure'));await operationB;assert.equal(f.submissions.length,0);
  currentA.resolve('topic-current-A');await operationCurrent;
  assert.equal(f.submissions.length,1);assert.equal(f.submissions[0].request.captionTopicKey,'topic-current-A');
  assert.equal(snapshotText(f.submissions[0]),'Alpha current');
});

test('actual capture rejects hashes across hidden and clear boundaries, then restores only the current caption',async()=>{
  const f=fixture();f.lifecycle.attach();f.setCaption('Before hidden');const hiddenHash=f.nextHash();
  const pending=f.capture.capture();await flush();f.doc.hidden=true;f.doc.dispatchEvent(new Event('visibilitychange'));
  hiddenHash.resolve('hidden-topic');await pending;assert.equal(f.submissions.length,0);
  f.setCaption('After restore');const restoredHash=f.nextHash();f.doc.hidden=false;f.doc.dispatchEvent(new Event('visibilitychange'));await flush();
  restoredHash.resolve('restored-topic');await flush();
  assert.equal(f.submissions.length,1);assert.equal(f.submissions[0].request.captionTopicKey,'restored-topic');
  assert.equal(snapshotText(f.submissions[0]),'After restore');

  f.lifecycle.dispose();
});

test('actual capture rejects an in-flight hash cleared by an empty live source',async()=>{
  const f=fixture(),clearHash=f.nextHash();f.setCaption('Before clear');const pending=f.capture.capture();await flush();
  f.setCaption('');await f.capture.capture();clearHash.resolve('clear-topic');await pending;assert.equal(f.submissions.length,0);
  f.setCaption('Current after clear');const currentHash=f.nextHash();const current=f.capture.capture();await flush();
  currentHash.resolve('current-topic');await current;
  assert.equal(f.submissions.length,1);assert.equal(f.submissions[0].request.captionTopicKey,'current-topic');
  assert.equal(snapshotText(f.submissions[0]),'Current after clear');f.lifecycle.dispose();
});


test('a short visible empty gap while hashing does not strand the returning identical caption',async()=>{
  const f=fixture(),hash=f.nextHash();f.setCaption('Returning caption');
  const pending=f.capture.capture();await flush();f.setGap(true);f.setCaption('');
  await f.capture.capture();hash.resolve('same-page-topic');await pending;
  assert.equal(f.submissions.length,0);
  f.setCaption('Returning caption');await f.capture.capture();
  assert.equal(f.hashCalls,1);assert.equal(f.submissions.length,1);
  assert.equal(snapshotText(f.submissions[0]),'Returning caption');
});
