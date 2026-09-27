export const evidence = { lexiconEntryId: '00000000-0000-0000-0000-000000000001', lexiconVersion: 1, senseId: '00000000-0000-0000-0000-000000000002' };
export const segment = (key,text,append=true,line=0) => ({key,text,append,line,offsetMs:null});
export const snapshot = (...segments) => ({captions:segments.length?[{windowId:null,startMs:null,segments}]:[]});
export const request = (currentSnapshot=snapshot(segment('s1','reliable')),lastRequestedSnapshot=null) => ({captionTopicKey:'synthetic-topic',trackKey:null,currentSnapshot,lastRequestedSnapshot});
export const response = (req,hints=[]) => ({processedKeys:req.currentSnapshot.captions.flatMap(g=>g.segments).filter(s=>s.append).map(s=>s.key),hints});
export const keyedHint = (startKey='s1',startOffset=0,endOffset=8,endKey=startKey) => ({...evidence,startKey,endKey,startOffset,endOffset,chineseGloss:'可靠的'});
