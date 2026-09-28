import assert from 'node:assert/strict';
import test from 'node:test';
import {readCaptionSource} from '../dist/caption-source.js';
const segment=(text,row=null,top=0)=>({innerText:text,closest:selector=>selector === ".caption-visual-line" ? row : null,getBoundingClientRect:()=>({top})});
test('keeps source visual rows without splitting inline segments or changing API offsets',()=>{
 const first={},second={};
 assert.deepEqual(readCaptionSource([segment('A reliable',first),segment('caption',first),segment('on two rows.',second,20)]),{caption:'A reliable caption on two rows.',lineBreaks:[19]});
});
test('handles explicit line breaks, Unicode and missing visual row wrappers',()=>{
 assert.deepEqual(readCaptionSource([segment('  α \n reliable ')]),{caption:'α reliable',lineBreaks:[2]});
 assert.deepEqual(readCaptionSource([segment('one',null,0),segment('two',null,24)]),{caption:'one two',lineBreaks:[4]});
 assert.equal(readCaptionSource([segment('  ')]),undefined);
});

const {CaptionRowViewport}=await import('../dist/caption-source.js');
const ids=[{},{},{}];
const roll=(shift=0)=>ids.map((id,index)=>({id,text:`L${index+1}`,top:index*24+shift,bottom:(index+1)*24+shift}));
test('roll-up selects the newest viewport-sized rows without a three-line intermediate frame',()=>{
 const viewport=new CaptionRowViewport();
 assert.deepEqual(viewport.select(roll(),0,48),ids.slice(0,2));
 assert.deepEqual(viewport.select(roll(-2),0,48),ids.slice(1));
 for(const shift of [-6,-12,-23,-24]) assert.deepEqual(viewport.select(roll(shift),0,48),ids.slice(1));
 // Animation reset, temporary three-row window, and later DOM prefix removal.
 assert.deepEqual(viewport.select(roll(),0,48),ids.slice(1));
 assert.deepEqual(viewport.select(roll(),0,72),ids.slice(1));
 assert.deepEqual(viewport.select(roll().slice(1).map(row=>({...row,top:row.top-24,bottom:row.bottom-24})),0,48),ids.slice(1));
});
test('retirement is node-and-content local, not a ban on recurring caption text',()=>{
 const viewport=new CaptionRowViewport();viewport.select(roll(),0,48);viewport.select(roll(-24),0,48);
 assert.deepEqual(viewport.select(roll().map(row=>({...row,id:{}})),0,48).length,2);
 const changed=new CaptionRowViewport();changed.select(roll(),0,48);changed.select(roll(-24),0,48);
 assert.deepEqual(changed.select(roll().map(row=>({...row,text:`new ${row.text}`})),0,48),ids.slice(0,2));
 assert.deepEqual(new CaptionRowViewport().select(roll(),0,48),ids.slice(0,2));
});
test('empty, unrelated, and fully clipped sources cannot resurrect retained rows',()=>{
 const viewport=new CaptionRowViewport();viewport.select(roll(),0,48);viewport.select(roll(-24),0,48);
 assert.deepEqual(viewport.select([],0,48),[]);
 assert.deepEqual(viewport.select(roll(),0,48),ids.slice(0,2));
 assert.deepEqual(viewport.select(roll(100),0,48),[]);
 assert.deepEqual(viewport.select([{id:{},text:'new',top:0,bottom:24}],0,48).length,1);
});
test('variable-height rows fit the window; one oversized row is not dropped',()=>{
 const viewport=new CaptionRowViewport();
 assert.deepEqual(viewport.select([{id:ids[0],text:'wrapped',top:-1,bottom:47},{id:ids[1],text:'next',top:47,bottom:71}],0,48),[ids[1]]);
 assert.deepEqual(new CaptionRowViewport().select([{id:ids[0],text:'tall',top:0,bottom:96}],0,48),[ids[0]]);
});
