import assert from 'node:assert/strict';
import test from 'node:test';
import {extractIncrementalText,rememberBounded} from '../dist/caption-debug.js';

test('incremental debug text contains English even when no hint is presented',()=>{
  const line={start:0,end:18,caption:'the helpful method',hints:[]};
  assert.equal(extractIncrementalText(line,[{start:4,end:18}]),'helpful method');
});
test('incremental debug includes only the necessary old phrase prefix and actual visible gloss',()=>{
  const line={start:0,end:18,caption:'the helpful method',hints:[{startOffset:4,endOffset:18,chineseGloss:'有用的方法'}]};
  assert.equal(extractIncrementalText(line,[{start:12,end:18}]),'helpful method(有用的方法)');
});
test('incremental debug does not include unrelated old text or suppressed hints',()=>{
  const line={start:0,end:15,caption:'known new words',hints:[]};
  assert.equal(extractIncrementalText(line,[{start:6,end:16}]),'new words');
});

test('debug deduplication memory remains bounded',()=>{
  const values=new Set();
  for(let index=0;index<100;index++) rememberBounded(values,index,16);
  assert.equal(values.size,16); assert.equal(values.has(0),false); assert.equal(values.has(99),true);
});
