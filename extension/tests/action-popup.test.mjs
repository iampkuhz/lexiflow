import assert from 'node:assert/strict';
import test from 'node:test';
import {popupClickPoint} from './action-popup.mjs';

function fixture(run) {
  const previous=globalThis.document;
  const child={};
  const element={disabled:false,scrollIntoView(){},contains(node){return node===child;},
    getBoundingClientRect(){return {left:10,top:30,width:40,height:20};}};
  const doc={querySelector(){return element;},elementFromPoint(){return element;}};
  globalThis.document=doc;
  try {run({element,doc,child});}
  finally {
    if(previous===undefined) delete globalThis.document;
    else globalThis.document=previous;
  }
}

test('popup click waits until an expanded control enters the viewport',()=>fixture(({doc,element})=>{
  doc.elementFromPoint=()=>null;
  assert.equal(popupClickPoint('#restore-start'),null);
  doc.elementFromPoint=()=>element;
  assert.deepEqual(popupClickPoint('#restore-start'),{x:30,y:40});
}));

test('popup click refuses disabled hidden or covered controls',()=>fixture(({doc,element})=>{
  element.disabled=true;
  assert.equal(popupClickPoint('#restore-start'),null);
  element.disabled=false;
  doc.elementFromPoint=()=>({});
  assert.equal(popupClickPoint('#restore-start'),null);
  doc.elementFromPoint=()=>element;
  element.getBoundingClientRect=()=>({left:0,top:0,width:0,height:0});
  assert.equal(popupClickPoint('#restore-start'),null);
}));

test('popup click accepts a visible descendant but not a missing control',()=>fixture(({doc,child})=>{
  doc.elementFromPoint=()=>child;
  assert.deepEqual(popupClickPoint('#preferences summary'),{x:30,y:40});
  doc.querySelector=()=>null;
  assert.equal(popupClickPoint('#restore-start'),null);
}));
