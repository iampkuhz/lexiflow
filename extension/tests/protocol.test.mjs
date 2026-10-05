import assert from 'node:assert/strict';
import test from 'node:test';
import {isCaptionHintRequest,parseHintResponse,resolveHint,inlineParts} from '../dist/protocol.js';
import {request,snapshot,segment,response,keyedHint} from './caption-fixtures.mjs';
test('accepts bounded dual snapshots and rejects old contract, duplicate identities and invalid time',()=>{
 assert.ok(isCaptionHintRequest(request()));
 for(const bad of [{caption:'reliable'},request(snapshot(segment('s','x'.repeat(501)))),request(snapshot(segment('s','a'),segment('s','b'))),
  request({captions:[{windowId:null,startMs:-1,segments:[segment('s','a')]}]}),request(snapshot({...segment('s','a'),offsetMs:-1})),request(snapshot({...segment('s','a'),append:null}))]) assert.equal(isCaptionHintRequest(bad),false);
 assert.ok(isCaptionHintRequest(request(snapshot())));
});
test('requires complete processed coverage even when no hints, and rejects foreign results',()=>{
 const req=request(); assert.deepEqual(parseHintResponse(response(req),req),response(req));
 for(const processedKeys of [[],['other'],['s1','s1']]) assert.equal(parseHintResponse({processedKeys,hints:[]},req),undefined);
 assert.equal(parseHintResponse(response(req,[keyedHint('other')]),req),undefined);
});
test('retains evidence, exact keyed offsets, Chinese safety and surrogate boundaries',()=>{
 const req=request(); const valid=response(req,[keyedHint()]); assert.deepEqual(parseHintResponse(valid,req),valid);
 for(const fields of [{chineseGloss:'任务，归因'},{chineseGloss:'\n任务'},{chineseGloss:'任务;作业'},{chineseGloss:'x'},{chineseGloss:'释'.repeat(25)},
 {lexiconEntryId:'unknown'},{senseId:null},{lexiconVersion:0},{endOffset:99},{startOffset:-1},{startOffset:1.5}])
 assert.equal(parseHintResponse(response(req,[{...keyedHint(),...fields}]),req),undefined);
 const emoji=request(snapshot(segment('s1','😀a'))); assert.equal(parseHintResponse(response(emoji,[keyedHint('s1',1,2)]),emoji),undefined);
});
test('allows bounded old-tail phrase completion but not old-only, distant or cross-group hints',()=>{
 const req=request(snapshot(segment('a','reliable'),segment('b',' methods'))),hint=keyedHint('a',0,8,'b');
 assert.ok(parseHintResponse(response(req,[hint]),req));
 assert.equal(resolveHint(hint,req.currentSnapshot).endOffset,16);
 req.currentSnapshot.captions[0].segments[0].append=false;
 assert.ok(parseHintResponse(response(req,[hint]),req));
 req.currentSnapshot.captions[0].segments[1].append=false;
 assert.equal(parseHintResponse(response(req,[hint]),req),undefined);
 const distant=request(snapshot(segment('old','far ',false),segment('gap','middle ',false),segment('more','context ',false),segment('new','word')));
 assert.equal(parseHintResponse(response(distant,[keyedHint('old',0,4,'new')]),distant),undefined);
 const separate=request({captions:[snapshot(segment('a','reliable')).captions[0],snapshot(segment('b',' methods')).captions[0]]});
 assert.equal(parseHintResponse(response(separate,[hint]),separate),undefined);
 const separateRows=request(snapshot(segment('a','reliable',false,0),segment('b',' methods',true,1)));
 assert.equal(parseHintResponse(response(separateRows,[hint]),separateRows),undefined);
});
test('rejects overlaps and mixed versions without imposing three-hint limit',()=>{
 const req=request(snapshot(...['a','b','c','d'].map(k=>segment(k,' word'))));
 const hints=['a','b','c','d'].map(k=>keyedHint(k,1,5));
 assert.equal(parseHintResponse(response(req,hints),req).hints.length,4);
 assert.equal(parseHintResponse(response(req,[hints[0],hints[0]]),req),undefined);
 assert.equal(parseHintResponse(response(req,[hints[0],{...hints[1],lexiconVersion:2}]),req),undefined);
 assert.equal(inlineParts('reliable',[resolveHint(keyedHint(),request().currentSnapshot)]).map(p=>p.text).join(''),'reliable(可靠的)');
});

test('preserves positive BIGINT entry identities as exact decimal strings',()=>{
 const req=request();
 for(const id of ['1','9007199254740993','9223372036854775807']) {
  const parsed=parseHintResponse(response(req,[{...keyedHint(),lexiconEntryId:id}]),req);
  assert.equal(parsed.hints[0].lexiconEntryId,id);
 }
 for(const id of ['0','01','-1','1.0','1e3','9223372036854775808','10000000000000000000',
  '00000000-0000-0000-0000-000000000001',9007199254740992,null]) {
  assert.equal(parseHintResponse(response(req,[{...keyedHint(),lexiconEntryId:id}]),req),undefined);
 }
});
