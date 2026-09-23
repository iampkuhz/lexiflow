import assert from 'node:assert/strict';
import test from 'node:test';
import {readCaptionSource} from '../dist/caption-source.js';
const segment=(text,row=null,top=0)=>({innerText:text,closest:()=>row,getBoundingClientRect:()=>({top})});
test('keeps source visual rows without splitting inline segments or changing API offsets',()=>{
 const first={},second={};
 assert.deepEqual(readCaptionSource([segment('A reliable',first),segment('caption',first),segment('on two rows.',second,20)]),{caption:'A reliable caption on two rows.',lineBreaks:[19]});
});
test('handles explicit line breaks, Unicode and missing visual row wrappers',()=>{
 assert.deepEqual(readCaptionSource([segment('  α \n reliable ')]),{caption:'α reliable',lineBreaks:[2]});
 assert.deepEqual(readCaptionSource([segment('one',null,0),segment('two',null,24)]),{caption:'one two',lineBreaks:[4]});
 assert.equal(readCaptionSource([segment('  ')]),undefined);
});
