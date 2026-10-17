import assert from 'node:assert/strict';
import test from 'node:test';
import {openChromeActionPopup,openInUniqueNormalWindow} from './action-popup.mjs';

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
  globalThis.chrome={action:{openPopup:async()=>{throw failure;}}};
  try {
    const serviceWorker={evaluate:(fn,arg)=>fn(arg)};
    await assert.rejects(openChromeActionPopup({windows:[{id:17,type:'normal'}],serviceWorker}),error=>error===failure);
  } finally { if(previous===undefined) delete globalThis.chrome; else globalThis.chrome=previous; }
});

test('passes the selected actual window ID to the Chrome action API',async()=>{
  const previous=globalThis.chrome;let received;
  globalThis.chrome={action:{openPopup:async options=>{received=options;}}};
  try {
    const serviceWorker={evaluate:(fn,arg)=>fn(arg)};
    await openChromeActionPopup({windows:[{id:41,type:'normal'}],serviceWorker});
    assert.deepEqual(received,{windowId:41});
  } finally { if(previous===undefined) delete globalThis.chrome; else globalThis.chrome=previous; }
});
