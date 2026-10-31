import assert from 'node:assert/strict';
import test from 'node:test';
import {measurePopupClickTarget,openChromeActionPopup,openInUniqueNormalWindow,waitForPopupClickTarget} from './action-popup.mjs';

test('opens against the sole normal window ID',async()=>{
  let selected;
  await openInUniqueNormalWindow({windows:[{id:17,type:'normal'},{id:18,type:'popup'}],openPopup:async id=>{selected=id;}});
  assert.equal(selected,17);
});

test('rejects missing, invalid, or ambiguous normal windows without opening',async()=>{
  for(const windows of [[],[{id:undefined,type:'normal'}],[{id:17,type:'normal'},{id:18,type:'normal'}]]) {
    let opened=false;
    await assert.rejects(openInUniqueNormalWindow({windows,openPopup:async()=>{opened=true;}}),/exactly one normal Chrome window/);
    assert.equal(opened,false);
  }
  await assert.rejects(openInUniqueNormalWindow({windows:null,openPopup:async()=>assert.fail()}),/windows unavailable/);
});

test('propagates the real action.openPopup error unchanged',async()=>{
  const failure=new Error('Could not find an active browser window');
  const previous=globalThis.chrome;
  globalThis.chrome={windows:{update:async()=>{}},action:{openPopup:async()=>{throw failure;}}};
  try {
    const serviceWorker={evaluate:(fn,arg)=>fn(arg)};
    await assert.rejects(openChromeActionPopup({windows:[{id:17,type:'normal'}],serviceWorker}),error=>error===failure);
  } finally { if(previous===undefined) delete globalThis.chrome; else globalThis.chrome=previous; }
});

test('focuses the selected actual window before passing its ID to the Chrome action API',async()=>{
  const previous=globalThis.chrome;const calls=[];
  globalThis.chrome={windows:{update:async(id,options)=>{calls.push(['focus',id,options]);}},action:{openPopup:async options=>{calls.push(['open',options]);}}};
  try {
    const serviceWorker={evaluate:(fn,arg)=>fn(arg)};
    await openChromeActionPopup({windows:[{id:41,type:'normal'}],serviceWorker});
    assert.deepEqual(calls,[['focus',41,{focused:true}],['open',{windowId:41}]]);
  } finally { if(previous===undefined) delete globalThis.chrome; else globalThis.chrome=previous; }
});

test('waits for the focus operation to settle before opening the popup',async()=>{
  const previous=globalThis.chrome;const calls=[];let finishFocus;
  const focusPending=new Promise(resolve=>{finishFocus=resolve;});
  globalThis.chrome={windows:{update:async()=>{calls.push('focus-start');await focusPending;calls.push('focus-finished');}},action:{openPopup:async()=>{calls.push('open');}}};
  let pending;
  try {
    pending=openChromeActionPopup({windows:[{id:41,type:'normal'}],serviceWorker:{evaluate:(fn,arg)=>fn(arg)}});
    await Promise.resolve();
    assert.deepEqual(calls,['focus-start']);
    finishFocus();await pending;
    assert.deepEqual(calls,['focus-start','focus-finished','open']);
  } finally { finishFocus();await pending;if(previous===undefined) delete globalThis.chrome;else globalThis.chrome=previous; }
});

test('propagates a focus failure without attempting to open the popup',async()=>{
  const previous=globalThis.chrome;const failure=new Error('window focus refused');let opened=false;
  globalThis.chrome={windows:{update:async()=>{throw failure;}},action:{openPopup:async()=>{opened=true;}}};
  try {
    await assert.rejects(openChromeActionPopup({windows:[{id:41,type:'normal'}],serviceWorker:{evaluate:(fn,arg)=>fn(arg)}}),error=>error===failure);
    assert.equal(opened,false);
  } finally { if(previous===undefined) delete globalThis.chrome;else globalThis.chrome=previous; }
});

test('reacquires focus only after Chrome explicitly rejects an inactive owned window',async()=>{
  const previous=globalThis.chrome;const calls=[];let attempts=0;
  const inactive=new Error('Cannot show popup for an inactive window. To show the popup for this window, first call `chrome.windows.update` with `focused` set to true.');
  globalThis.chrome={windows:{update:async(id,options)=>{calls.push(['focus',id,options]);}},action:{openPopup:async options=>{
    calls.push(['open',options]);if(++attempts<3) throw inactive;return 'opened';
  }}};
  try {
    assert.equal(await openChromeActionPopup({windows:[{id:41,type:'normal'}],serviceWorker:{evaluate:(fn,arg)=>fn(arg)}}),'opened');
    assert.equal(attempts,3);
    assert.deepEqual(calls,Array.from({length:3},()=>[['focus',41,{focused:true}],['open',{windowId:41}]]).flat());
  } finally {if(previous===undefined) delete globalThis.chrome;else globalThis.chrome=previous;}
});

test('persistent inactive-window rejection stops after three attempts and preserves the error',async()=>{
  const previous=globalThis.chrome;let focus=0,open=0;
  const inactive=new Error('Cannot show popup for an inactive window.');
  globalThis.chrome={windows:{update:async()=>{focus++;}},action:{openPopup:async()=>{open++;throw inactive;}}};
  try {
    await assert.rejects(openChromeActionPopup({windows:[{id:41,type:'normal'}],serviceWorker:{evaluate:(fn,arg)=>fn(arg)}}),error=>error===inactive);
    assert.equal(focus,3);assert.equal(open,3);
  } finally {if(previous===undefined) delete globalThis.chrome;else globalThis.chrome=previous;}
});

test('popup click measurement rejects hidden, disabled, clipped, zero-size and obstructed targets',()=>{
  const previous={document:globalThis.document,getComputedStyle:globalThis.getComputedStyle,innerWidth:globalThis.innerWidth,innerHeight:globalThis.innerHeight,scrollX:globalThis.scrollX,scrollY:globalThis.scrollY};
  const element={disabled:false,rect:{left:10,top:20,right:110,bottom:50,width:100,height:30},style:{display:'block',visibility:'visible',opacity:'1'},aria:null,
    getBoundingClientRect(){return this.rect;},getClientRects(){return this.style.display==='none'?[]:[this.rect];},getAttribute(name){return name==='aria-disabled'?this.aria:null;},contains(node){return node===this;}};
  let hit=element;
  globalThis.document={querySelector:()=>element,elementFromPoint:()=>hit};
  globalThis.getComputedStyle=()=>element.style;globalThis.innerWidth=300;globalThis.innerHeight=200;globalThis.scrollX=0;globalThis.scrollY=0;
  try {
    assert.deepEqual(measurePopupClickTarget('#target'),{x:60,y:35,left:10,top:20,width:100,height:30,viewportWidth:300,viewportHeight:200,scrollX:0,scrollY:0});
    for(const mutate of [()=>{element.style.display='none';},()=>{element.style.display='block';element.disabled=true;},()=>{element.disabled=false;element.rect={left:-1,top:20,right:99,bottom:50,width:100,height:30};},()=>{element.rect={left:10,top:20,right:10,bottom:20,width:0,height:0};},()=>{element.rect={left:10,top:20,right:110,bottom:50,width:100,height:30};hit={};}]) {
      mutate();assert.equal(measurePopupClickTarget('#target'),null);
      element.style.display='block';element.disabled=false;element.rect={left:10,top:20,right:110,bottom:50,width:100,height:30};hit=element;
    }
    element.aria='true';assert.equal(measurePopupClickTarget('#target'),null);
  } finally {
    for(const [key,value] of Object.entries(previous)) {if(value===undefined) delete globalThis[key];else globalThis[key]=value;}
  }
});

test('waits through unstable popup layout and returns only the settled measured coordinate',async()=>{
  const samples=[null,{x:40,y:30,left:0,top:0,width:80,height:60,viewportWidth:300,viewportHeight:200,scrollX:0,scrollY:0},
    {x:50,y:30,left:10,top:0,width:80,height:60,viewportWidth:300,viewportHeight:200,scrollX:0,scrollY:0},
    {x:50,y:30,left:10,top:0,width:80,height:60,viewportWidth:300,viewportHeight:200,scrollX:0,scrollY:0},
    {x:50,y:30,left:10,top:0,width:80,height:60,viewportWidth:300,viewportHeight:200,scrollX:0,scrollY:0},
    {x:50,y:30,left:10,top:0,width:80,height:60,viewportWidth:300,viewportHeight:200,scrollX:0,scrollY:0}];
  let calls=0;
  const point=await waitForPopupClickTarget('#target',async(fn,selector)=>{assert.equal(selector,'#target');calls++;return samples.shift()??null;});
  assert.deepEqual(point,{x:50,y:30});assert.equal(calls,5);
});
