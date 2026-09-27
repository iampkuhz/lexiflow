import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {YoutubeSourceMetadata} from '../dist/youtube-source.js';
const track={videoId:'synthetic',trackKey:'en:asr::',fragments:[
 {text:'A',startMs:10000,endMs:13000,offsetMs:0,windowId:'1',append:false},
 {text:' reliable',startMs:10000,endMs:13000,offsetMs:300,windowId:'1',append:true},
 {text:' method',startMs:10000,endMs:13000,offsetMs:700,windowId:'1',append:true}]};
test('maps only active DOM-matched source metadata and never invents unknown timings',()=>{
 const source=new YoutubeSourceMetadata();source.accept(track,'synthetic');
 const match=source.match('A reliable',10350);assert.equal(match.trackKey,track.trackKey);assert.deepEqual(match.metadata(' reliable'),{windowId:'1',startMs:10000,offsetMs:300});
 assert.equal(source.match('method',10350).trackKey,null);assert.equal(source.match('A reliable',14000).metadata(' reliable').startMs,null);
 source.accept({...track,trackKey:'another'},'synthetic');assert.equal(source.match('A reliable',10350).trackKey,null);
 source.reset();assert.equal(source.match('A reliable',10350).trackKey,null);
});
test('rejects spoofed or malformed source evidence at the isolated-world boundary',()=>{
 const source=new YoutubeSourceMetadata();
 for(const value of [{...track,videoId:'other'},{...track,fragments:[{...track.fragments[0],offsetMs:-1}]},{...track,fragments:[{...track.fragments[0],text:42}]}]) source.accept(value,'synthetic');
 assert.equal(source.match('A',10100).trackKey,null);
});
test('MAIN bridge observes JSON3 fetch without another request and derives append/timing',async()=>{
 const code=await readFile(new URL('../dist/youtube-bridge.js',import.meta.url),'utf8');const messages=[];let calls=0;
 const raw={events:[{wWinId:1,tStartMs:10000,dDurationMs:3000,aAppend:0,segs:[{utf8:'A'},{utf8:' reliable',tOffsetMs:300}]}]};
 const win={fetch:async()=>{calls++;return{url:'https://www.youtube.com/api/timedtext?v=synthetic&lang=en&kind=asr',clone:()=>({text:async()=>JSON.stringify(raw)})};},postMessage:m=>messages.push(m),addEventListener:()=>{}};
 class XHR {open(){} addEventListener(){}}
 vm.runInNewContext(code,{window:win,document:{querySelector:()=>({currentTime:10}),addEventListener:()=>{}},location:{href:'https://www.youtube.com/watch?v=synthetic',origin:'https://www.youtube.com'},XMLHttpRequest:XHR,URL});
 await win.fetch('/api/timedtext');for(let i=0;i<4;i++)await Promise.resolve();assert.equal(calls,1);assert.equal(messages.length,1);
 assert.equal(messages[0].track.fragments[0].offsetMs,null);assert.equal(messages[0].track.fragments[0].append,false);assert.equal(messages[0].track.fragments[1].append,true);assert.equal(messages[0].track.fragments[1].offsetMs,300);
 assert.equal(JSON.stringify(messages).includes('watch?v'),false);
});

test('missing segment offset stays unknown while explicit zero remains a real source value',()=>{
 const source=new YoutubeSourceMetadata();source.accept({...track,fragments:[{...track.fragments[0],offsetMs:null}]},'synthetic');
 assert.deepEqual(source.match('A',10100).metadata('A'),{windowId:'1',startMs:10000,offsetMs:null});
 source.accept({...track,fragments:[{...track.fragments[0],offsetMs:0}]},'synthetic');
 assert.equal(source.match('A',10100).metadata('A').offsetMs,0);
});
test('MAIN bridge rejects missing or invalid source intervals rather than inventing times',async()=>{
 const code=await readFile(new URL('../dist/youtube-bridge.js',import.meta.url),'utf8');const messages=[];
 const raw={events:[
  {dDurationMs:3000,segs:[{utf8:'missing start'}]},
  {tStartMs:10000,segs:[{utf8:'missing duration'}]},
  {tStartMs:10000,dDurationMs:0,segs:[{utf8:'zero duration'}]},
  {tStartMs:Number.MAX_SAFE_INTEGER,dDurationMs:3000,segs:[{utf8:'overflow'}]},
  {tStartMs:10000,dDurationMs:3000,segs:[{utf8:'valid'}]}]};
 const win={fetch:async()=>({url:'https://www.youtube.com/api/timedtext?v=synthetic&lang=en',clone:()=>({text:async()=>JSON.stringify(raw)})}),postMessage:m=>messages.push(m),addEventListener:()=>{}};
 class XHR {open(){} addEventListener(){}}
 vm.runInNewContext(code,{window:win,document:{querySelector:()=>({currentTime:10}),addEventListener:()=>{}},location:{href:'https://www.youtube.com/watch?v=synthetic',origin:'https://www.youtube.com'},XMLHttpRequest:XHR,URL});
 await win.fetch('/api/timedtext');for(let i=0;i<4;i++)await Promise.resolve();
 assert.equal(messages.length,1);assert.equal(messages[0].track.fragments.length,1);
 assert.equal(messages[0].track.fragments[0].text,'valid');assert.equal(messages[0].track.fragments[0].startMs,10000);assert.equal(messages[0].track.fragments[0].offsetMs,null);
});
