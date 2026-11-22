export const segment = (key,text,append=true,line=0) => ({key,text,append,line});
export const snapshot = (...segments) => ({captions:segments.length?[{segments}]:[]});
export const baseline = snapshot => snapshot.captions.flatMap(group=>group.segments).map(({key,text})=>({key,text}));
export const request = (currentSnapshot=snapshot(segment('s1','reliable')),previousSegments=[]) => ({previousSegments,currentSnapshot});
export const observation = currentSnapshot => ({
  captionTopicKey:'synthetic-topic',trackKey:null,currentSnapshot:{captions:currentSnapshot.captions.map(({segments})=>({
    windowId:'source-window',startMs:100,segments:segments.map(segment=>({...segment,offsetMs:10}))
  }))}
});
export const response = (req,hints=[],lexiconVersion=1) => ({
  processedKeys:req.currentSnapshot.captions.flatMap(g=>g.segments).filter(s=>s.append).map(s=>s.key),
  lexiconVersion,hints
});
export const keyedHint = (startKey='s1',startOffset=0,endOffset=8,endKey=startKey) => ({
  lexiconEntryId:'9007199254740993',frequencyRank:null,startKey,endKey,startOffset,endOffset,chineseGloss:'可靠的'
});
