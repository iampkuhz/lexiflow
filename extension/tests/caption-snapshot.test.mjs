import assert from 'node:assert/strict';
import test from 'node:test';
import {CaptionSnapshotTracker,retainedOverlap} from '../dist/caption-snapshot.js';
import {snapshotSegments} from '../dist/protocol.js';
const capture=(tracker,caption,lineBreaks=[])=>snapshotSegments(tracker.capture({caption,lineBreaks}));
test('stable keys survive append, rollup, repeated words and DOM-independent reflow',()=>{
 const tracker=new CaptionSnapshotTracker('page');const a=capture(tracker,'go go');const b=capture(tracker,'go go again',[3]);
 assert.equal(b[0].key,a[0].key);assert.equal(b[1].key,a[1].key);assert.notEqual(a[0].key,a[1].key);assert.equal(b[1].line,1);
 const c=capture(tracker,'go again');assert.equal(c[0].key,b[1].key);assert.equal(c[0].text,'go');assert.equal(c[1].key,b[2].key);
 tracker.reset();assert.notEqual(capture(tracker,'go')[0].key,a[0].key);
});
test('growing source node splits new suffix and snapshots are immutable',()=>{
 const tracker=new CaptionSnapshotTracker('page'),a=capture(tracker,'A reliable');const b=capture(tracker,'A reliable method');
 assert.equal(a.map(s=>s.text).join(''),'A reliable');assert.equal(b.at(-1).text,' method');assert.notEqual(b.at(-1).key,a.at(-1).key);
 b[0].key='tampered';assert.notEqual(capture(tracker,'A reliable method!')[0].key,'tampered');
});
test('does not split surrogate pairs at overlap boundary',()=>{assert.equal(retainedOverlap('a😀','😀b'),2);assert.equal(retainedOverlap('😀','\ude00x'),0);});
test('keeps separate unknown windows and resets display line numbering within each window',()=>{
 const tracker=new CaptionSnapshotTracker('page');const captured=tracker.capture({caption:'A reliable method',lineBreaks:[2,11],windowBreaks:[10]});
 assert.equal(captured.captions.length,2);assert.equal(captured.captions[0].segments[1].line,1);assert.equal(captured.captions[1].segments[0].line,0);
});
