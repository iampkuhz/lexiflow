import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { createPageLifecycle } from '../dist/page-lifecycle.js';

const original = {};
for (const key of ['window','document','MutationObserver','requestAnimationFrame','cancelAnimationFrame','Element']) original[key] = globalThis[key];
class Target { hidden=false; documentElement={}; listeners=new Map(); closest(selector){return selector==='#ytp-caption-window-container'?this:null;} addEventListener(name,callback){const entries=this.listeners.get(name)??[];entries.push(callback);this.listeners.set(name,entries);} removeEventListener(name,callback){this.listeners.set(name,(this.listeners.get(name)??[]).filter(item=>item!==callback));} dispatch(name){for(const callback of [...(this.listeners.get(name)??[])])callback({target:this});} }
class Observer {
  static instances=[];
  disconnected=false;
  constructor(callback){this.callback=callback;Observer.instances.push(this);}
  observe(){}
  disconnect(){this.disconnected=true;}
}
function environment(){
  const doc=new Target(),win=new Target();let next=0;const frames=new Map();
  globalThis.document=doc;globalThis.window=win;globalThis.MutationObserver=Observer;
  globalThis.requestAnimationFrame=callback=>{frames.set(++next,callback);return next;};
  globalThis.cancelAnimationFrame=id=>frames.delete(id);
  globalThis.Element=Target;
  Observer.instances=[];return {doc,win,frames};
}
afterEach(()=>{for(const [key,value] of Object.entries(original))globalThis[key]=value;});

test('navigation, seek, switch, hidden and page lifecycle invalidate, and pageshow re-reads the live source',()=>{
  const {doc,win}=environment();let video='A',player=true,invalidations=0,captures=0,key=0;
  const lifecycle=createPageLifecycle({currentVideoId:()=>video,hasPlayer:()=>player,hasCaptionMotion:()=>false},
    ()=>invalidations++,()=>captures++,()=>{},()=>`page-${++key}`);
  lifecycle.attach();const firstKey=lifecycle.pageKey,firstGeneration=lifecycle.generation;
  video='B';doc.dispatch('yt-navigate-start');
  assert.notEqual(lifecycle.pageKey,firstKey);assert.equal(lifecycle.videoId,'B');
  doc.dispatch('yt-navigate-finish');assert.ok(captures>0);
  const currentKey=lifecycle.pageKey,generation=lifecycle.generation;
  assert.equal(lifecycle.isCurrent(generation,'B'),true);
  assert.equal(lifecycle.setEnabled(currentKey,false),true);assert.equal(lifecycle.enabled,false);
  assert.equal(lifecycle.isCurrent(lifecycle.generation,'B'),false);
  assert.equal(lifecycle.setEnabled(currentKey,true),true);assert.equal(lifecycle.enabled,true);
  doc.hidden=true;doc.dispatch('visibilitychange');
  assert.equal(lifecycle.isCurrent(lifecycle.generation,'B'),false);
  doc.hidden=false;const beforeShow=captures;win.dispatch('pageshow');assert.ok(captures>beforeShow);
  doc.dispatch('seeking');assert.equal(lifecycle.seeking,true);
  doc.dispatch('seeked');assert.equal(lifecycle.seeking,false);
  win.dispatch('pagehide');assert.equal(lifecycle.stopped,true);
  win.dispatch('pageshow');assert.equal(lifecycle.stopped,false);
  video='A';lifecycle.refreshPage();video='B';lifecycle.refreshPage();video='A';lifecycle.refreshPage();
  assert.equal(lifecycle.isCurrent(firstGeneration,'A'),false,'same A video after new pages has a new generation');
  assert.ok(invalidations>=7);lifecycle.dispose();
});

test('dispose stops observer, listeners and a queued caption RAF exactly once',()=>{
  const {doc,frames}=environment();let captures=0,invalidations=0;
  const lifecycle=createPageLifecycle({currentVideoId:()=> 'A',hasPlayer:()=>true,hasCaptionMotion:()=>true},
    ()=>invalidations++,()=>captures++,()=>{},()=> 'page');
  lifecycle.attach();const initialCaptures=captures;
  doc.dispatch('transitionrun');assert.equal(frames.size,1);
  lifecycle.dispose();assert.equal(frames.size,0);assert.equal(Observer.instances[0].disconnected,true);
  const afterDispose=captures;doc.dispatch('timeupdate');
  doc.dispatch('transitionrun');
  assert.equal(captures,afterDispose);assert.equal(frames.size,0);
  const count=invalidations;lifecycle.dispose();assert.equal(invalidations,count);assert.ok(afterDispose>=initialCaptures);
});


test('navigation clears prior media stop and seek flags without waiting for old media events',()=>{
  const {doc}=environment();let video='A';
  const lifecycle=createPageLifecycle({currentVideoId:()=>video,hasPlayer:()=>true,hasCaptionMotion:()=>false},
    ()=>{},()=>{},()=>{},()=>crypto.randomUUID());
  lifecycle.attach();doc.dispatch('ended');doc.dispatch('seeking');
  video='B';doc.dispatch('yt-navigate-start');doc.dispatch('yt-navigate-finish');
  assert.equal(lifecycle.stopped,false);assert.equal(lifecycle.seeking,false);
  assert.equal(lifecycle.isCurrent(lifecycle.generation,'B'),true);lifecycle.dispose();
});
