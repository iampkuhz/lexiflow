import assert from 'node:assert/strict';
import test, {after} from 'node:test';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {chromium} from 'playwright';

const dist=resolve(import.meta.dirname,'../dist');
const chromePath=process.env.LEXIFLOW_CHROME_EXECUTABLE || (process.platform==='darwin'&&existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined);
const initial={recent:[{entryId:'101',lemma:'tarnish',gloss:'失泽',frequencyRank:2902,count:6},{entryId:'102',lemma:'protect',gloss:'保护',frequencyRank:null,count:4}],hidden:[{entryId:'103',lemma:'settlement',gloss:'安顿',frequencyRank:5200}]};
let browser;
after(async()=>{if(browser)await (await browser).close();});
async function openManage(t,{preferencesAvailable=true,clock=false,lexiconTotal,fixture=initial,pageOptions,hash=""}={}) {
 const instance=await (browser??=chromium.launch({headless:true,...(chromePath?{executablePath:chromePath}:{})}));
 const page=await instance.newPage(pageOptions);page.setDefaultTimeout(8_000);t.after(()=>page.context().close());
 if(clock) await page.clock.install({time:new Date('2026-11-10T00:00:10Z')});
 await page.addInitScript(({initial,preferencesAvailable,lexiconTotal})=>{
  const s={recent:structuredClone(initial.recent),hidden:structuredClone(initial.hidden),cutoff:1500,calls:[],lexPageSize:20,videoId:"LfxE2ETest1",videoAvailable:true,backend:"http://127.0.0.1:19081"};window.__state=s;
  const pref=()=>({ok:true,commonWordCutoff:s.cutoff,hidden:structuredClone(s.hidden)});
  const allLex=initial.lexicon??Array.from({length:26},(_,i)=>({entryId:String(200+i),lemma:String.fromCharCode(97+i)+'word',gloss:`释义 ${i}`,frequencyRank:i===25?null:i+1,hintState:i%4===0?'blocked':'eligible'}));
  const storage=()=>JSON.parse(localStorage.getItem("fixture:manage")??"{}");
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{
   if(s.copyOperation==="denied") throw new Error("fixture denied");
   if(s.copyOperation==="hold") await new Promise(resolve=>{s.finishCopy=resolve;});
   s.copied=text;
  }}});
  const runtime={softwareVersion:"3.0.0-SNAPSHOT.20261121.gcac57bd.dirty.123456789abc",apiContract:"caption-hints.v5",mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null,
     publishedDataset:null,privateInput:"fixture-secret"};
  window.chrome={tabs:{async sendMessage(tab,message){s.calls.push({tab,...message});
    return s.videoAvailable&&tab===42&&message.pageKey==="receipt"
     ? {ok:true,pageKey:"receipt",videoId:s.videoId,title:s.videoTitle,rows:s.videoRows??[
        {english:"protect",chinese:"保护",frequencyRank:null,hintCount:3},
        {english:"tarnish",chinese:"失泽",frequencyRank:2902,hintCount:1},
        {english:"zero",chinese:"零",frequencyRank:24,hintCount:0}]}
     : {ok:false};
  }},storage:{local:{get:async key=>({[key]:storage()[key]}),set:async value=>{
    localStorage.setItem("fixture:manage",JSON.stringify({...storage(),...value}));
  }}},runtime:{getManifest:()=>({version:"3.0.0"}),sendMessage:async m=>{s.calls.push(structuredClone(m));
   if(m.type==='runtime-status')return {ok:true,status:runtime,origin:s.backend};
   if(m.type==='backend-connection'){
    if(s.failConnection)return {ok:false,reason:"protocol-mismatch"};
    s.backend=m.origin;return {ok:true,status:runtime,origin:s.backend};
   }
   if(m.type==='local-preferences'&&m.action==='read')return preferencesAvailable?pref():{ok:false,reason:'network'};
   if(m.type==='local-preferences'&&m.action==='setCutoff'){s.cutoff=m.commonWordCutoff;return pref();}
   if(m.type==='local-recent')return s.recentUnavailable?{ok:false,reason:'network'}:{ok:true,items:structuredClone(s.recent).map(x=>({frequencyRank:null,...x})).sort((a,b)=>b.count-a.count)};
   if(m.type==='local-preferences'&&m.action==='hide'){const x=[...s.recent,...allLex].find(item=>item.entryId===m.entryId);if(x&&!s.hidden.some(h=>h.entryId===m.entryId))s.hidden.push({entryId:x.entryId,lemma:x.lemma,gloss:x.gloss,frequencyRank:x.frequencyRank??null});return pref();}
   if(m.type==='local-preferences'&&m.action==='restore'){s.hidden=s.hidden.filter(x=>x.entryId!==m.entryId);return pref();}
   if(m.type==='local-lexicon'){const q=m.query.toLowerCase();const found=allLex.filter(x=>x.lemma.includes(q));found.sort((a,b)=>m.sort==='alphabet'?a.lemma.localeCompare(b.lemma):(a.frequencyRank??Infinity)-(b.frequencyRank??Infinity));const start=(m.page-1)*m.pageSize;
    const items=found.slice(start,start+m.pageSize).map(x=>({...x,hintState:s.hidden.some(h=>h.entryId===x.entryId)?'hidden':x.hintState}));
    return {ok:true,items,total:lexiconTotal??found.length,page:m.page,pageSize:m.pageSize};}
   return {ok:false,reason:'unknown-message'};
  }}};
 },{initial:fixture,preferencesAvailable,lexiconTotal});
 const visibleTotal=fixture.recent.filter(item=>!preferencesAvailable || !fixture.hidden.some(x=>x.entryId===item.entryId)).length;
 await page.goto('file://'+resolve(dist,'manage.html')+hash);await page.waitForFunction(total=>document.querySelector('#recent-count').textContent===String(total),visibleTotal);return page;
}

const card = (page, id) => page.locator(`.recent-card[data-entry="${id}"]`);
const mutations = page => page.evaluate(()=>window.__state.calls.filter(x=>x.action==='hide').map(x=>x.entryId));

test('same-position double click confirms, filters the result across refresh and allows restoring it',async t=>{
 const page=await openManage(t);
 const first=card(page,'101'), second=card(page,'102');
 const firstSlot=await first.boundingBox();
 assert.equal(await first.locator('.hint-count').textContent(),'6 次');
 await first.locator('.hint-word').hover();
 const action=first.locator('.recent-action');
 await action.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
 const anchor=await action.boundingBox();
 await page.mouse.dblclick(anchor.x+anchor.width/2,anchor.y+anchor.height/2);
 await page.waitForFunction(()=>document.querySelector('#hidden-count').textContent==='2');
 assert.deepEqual(await mutations(page),['101']);
 assert.deepEqual(await page.evaluate(()=>window.__state.calls.filter(x=>x.action==='hide')),
   [{type:'local-preferences',action:'hide',entryId:'101'}]);
 assert.equal(await first.count(),0);
 assert.deepEqual(await second.boundingBox(),firstSlot);
 assert.equal(await second.getAttribute('data-pending'),null);
 assert.equal(await page.locator('#recent-count').textContent(),'1');
 const readsBefore=await page.evaluate(()=>window.__state.calls.filter(m=>m.type==='local-recent').length);
 await page.click('#hidden-tab');
 await page.click('#recent-tab');
 await page.waitForFunction(n=>window.__state.calls.filter(m=>m.type==='local-recent').length>n,readsBefore);
 assert.equal(await first.count(),0);
 await page.click('#hidden-tab');
 assert.equal(await page.locator('#hidden-list .hint-card').count(),2);
 await page.getByRole('button',{name:'恢复提示「tarnish」'}).click();
 await page.waitForFunction(()=>document.querySelector('#hidden-count').textContent==='1');
 assert.match(await page.locator('#feedback').textContent(),/已恢复/);
 await page.click('#recent-tab');
 assert.deepEqual(await page.locator('#recent-list .hint-word').allTextContents(),['tarnish','protect']);
});

test('first click swaps the count for equal left Undo and fixed right Confirm; undo does not pin the action',async t=>{
 const page=await openManage(t);
 const first=card(page,'101'), second=card(page,'102');
 assert.equal(await page.locator('#recent-panel input[type=checkbox], #hide-selected').count(),0);
 const action=first.locator('.recent-action'), undo=first.locator('.recent-undo');
 await first.locator('.hint-word').hover();
 await action.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
 const anchor=await action.boundingBox();
 await page.mouse.click(anchor.x+anchor.width/2,anchor.y+anchor.height/2);
 assert.deepEqual(await mutations(page),[]);
 assert.equal(await action.textContent(),'确认');
 assert.equal(await undo.textContent(),'撤销');
 assert.equal(await first.locator('.hint-count').isHidden(),true);
 assert.deepEqual(await action.boundingBox(),anchor);
 const left=await undo.boundingBox();
 assert.deepEqual([left.width,left.height,left.y],[anchor.width,anchor.height,anchor.y]);
 assert.ok(left.x+left.width<anchor.x);
 await undo.click();
 assert.equal(await undo.isHidden(),true);
 await second.locator('.hint-word').hover();
 await page.waitForFunction(()=>getComputedStyle(document.querySelector('.recent-card[data-entry="101"] .recent-action')).opacity==='0');
 assert.equal(await first.locator('.hint-count').isVisible(),true);
 assert.deepEqual(await mutations(page),[]);
 await second.getByRole('button',{name:'不再提示「protect」'}).click();
 await first.locator('.hint-word').hover();
 await action.click();
 assert.equal(await second.getAttribute('data-pending'),null);
 assert.equal(await first.getAttribute('data-pending'),'');
 assert.deepEqual(await mutations(page),[]);
});

test('recent shows only the seven-day ranking and refreshes on the minute and on return',async t=>{
 const page=await openManage(t,{clock:true});
 const rows=page.locator('#recent-list .recent-card');
 const names=()=>rows.locator('.hint-word').allTextContents();
 assert.equal(await page.locator('#recent-heading').textContent(),'近 7 天提示');
 assert.equal(await page.locator('#recent-page-size').inputValue(),'40');
 assert.deepEqual(await names(),['tarnish','protect']);
 await page.evaluate(()=>{window.__state.recent.shift();window.__state.recent[0].count=7;});
 await page.clock.fastForward(60_000);
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='1');
 assert.deepEqual(await names(),['protect']);
 assert.equal(await rows.locator('.hint-count').textContent(),'7 次');

 // A pending confirmation stays stable; leaving the tab and hiding the document stop the minute reads.
 await card(page,'102').locator('.hint-word').hover();
 await card(page,'102').getByRole('button',{name:'不再提示「protect」'}).click();
 const saved=await page.evaluate(()=>{const s=window.__state;return s.recent.pop();});
 await page.clock.fastForward(60_000);
 assert.equal(await card(page,'102').locator('.recent-action').textContent(),'确认');
 await card(page,'102').getByRole('button',{name:'撤销「protect」'}).click();
 await page.clock.fastForward(60_000);
 await page.waitForFunction(()=>!document.querySelector('#recent-empty').hidden);
 assert.match(await page.locator('#recent-empty').textContent(),/近 7 天/);

 const reads=()=>page.evaluate(()=>window.__state.calls.filter(m=>m.type==='local-recent').length);
 await page.click('#hidden-tab');
 const inactive=await reads();
 await page.clock.fastForward(60_000);
 assert.equal(await reads(),inactive);
 await page.evaluate(item=>{window.__state.recent.push(item);},saved);
 await page.click('#recent-tab');
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='1');
 assert.deepEqual(await names(),['protect']);

 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
 const hidden=await reads();
 await page.clock.fastForward(60_000);
 assert.equal(await reads(),hidden);
 await page.evaluate(()=>{
   window.__state.recent.push({entryId:'101',lemma:'tarnish',gloss:'失泽',count:8});
   Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});
   document.dispatchEvent(new Event('visibilitychange'));
 });
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='2');
 assert.deepEqual(await names(),['tarnish','protect']);
 assert.equal(await page.evaluate(()=>window.__state.calls.filter(x=>x.type==='local-recent').every(x=>Object.keys(x).length===1)),true);
});

test('unavailable recent statistics neither show stale counts nor pretend that the ranking is empty',async t=>{
 const page=await openManage(t,{clock:true});
 await page.evaluate(()=>{window.__state.recentUnavailable=true;});
 await page.clock.fastForward(60_000);
 await page.waitForFunction(()=>!document.querySelector('#recent-status').hidden);
 assert.match(await page.locator('#recent-status').textContent(),/无法读取近 7 天提示/);
 assert.equal(await page.locator('#recent-list').isHidden(),true);
 assert.equal(await page.locator('#recent-empty').isHidden(),true);
 assert.equal(await page.locator('#recent-count').textContent(),'');
 await page.evaluate(()=>{window.__state.recentUnavailable=false;});
 await page.clock.fastForward(60_000);
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='2');
 assert.equal(await page.locator('#recent-status').isHidden(),true);
});

for (const width of [1120,320]) {
 test(`cutoff explains BNC and keeps save feedback beside its control at ${width}px`,async t=>{
  const page=await openManage(t,{pageOptions:{viewport:{width,height:800}}});
  const select=page.getByLabel('不提示 BNC 高频前 N 词', { exact: true });
  const status=page.locator('#cutoff-status');
  assert.equal(await page.locator('h1 + section').getAttribute('id'),'preference-panel');
  assert.match(await page.locator('#cutoff-heading + #cutoff-source-note').textContent(),/英国国家语料库（BNC）/u);
  assert.equal(await select.inputValue(),'1500');
  await select.selectOption('2000');
  await page.waitForFunction(()=>document.querySelector('#cutoff-status').dataset.kind==='success');
  assert.equal(await status.textContent(),'已保存：前 2000 词。');
  assert.equal(await select.inputValue(),'2000');
  await page.evaluate(()=>{const send=window.chrome.runtime.sendMessage;window.chrome.runtime.sendMessage=async m=>m.type==='local-preferences'&&m.action==='setCutoff'?{ok:false,reason:'network'}:send(m);});
  await select.selectOption('1000');
  await page.waitForFunction(()=>document.querySelector('#cutoff-status').dataset.kind==='error');
  assert.match(await status.textContent(),/保存失败.*无法连接本机服务/u);
  assert.equal(await select.inputValue(),'2000');
  const [control,feedback,panel,heading]=await Promise.all([select,status,page.locator('#preference-panel'),page.locator('.cutoff-copy')].map(node=>node.boundingBox()));
  assert.ok(control.width>=150 && feedback.x+feedback.width<=panel.x+panel.width);
  if (width===320) assert.ok(feedback.x===control.x && feedback.y>=control.y+control.height);
  else {
   assert.ok(control.x-heading.x-heading.width<=24);
   assert.ok(feedback.x>=control.x+control.width && feedback.y<control.y+control.height);
  }
  assert.ok(feedback.y+feedback.height<=panel.y+panel.height);
 });
}

test('unavailable cutoff read does not present the first option as the saved value',async t=>{
 const page=await openManage(t,{preferencesAvailable:false});
 await page.waitForFunction(()=>document.querySelector('#cutoff-status').dataset.kind==='error');
 assert.equal(await page.locator('#common-word-cutoff').inputValue(),'');
 assert.equal(await page.locator('#common-word-cutoff').isDisabled(),true);
});

test('saving waits for a reply, disables duplicate mutation, and displays failure inline with a retry',async t=>{
 const page=await openManage(t);
 await page.evaluate(()=>{
  const send=window.chrome.runtime.sendMessage;
  window.chrome.runtime.sendMessage=m=>{
   if(m.action!=='hide')return send(m);
   window.__state.calls.push(m);
   return new Promise(resolve=>window.__rejectHide=()=>{window.chrome.runtime.sendMessage=send;resolve({ok:false,reason:'synthetic write failure'});});
  };
 });
 const first=card(page,'101'), action=first.locator('.recent-action');
 await first.locator('.hint-word').hover();
 await action.click();
 await action.click();
 await page.waitForFunction(()=>typeof window.__rejectHide==='function');
 const {x,y,width,height}=await action.boundingBox();
 await page.mouse.click(x+width/2,y+height/2);
 assert.equal(await action.getAttribute('aria-disabled'),'true');
 assert.deepEqual(await mutations(page),['101']);
 assert.equal(await first.count(),1);
 assert.equal(await page.locator('#recent-count').textContent(),'2');
 await page.evaluate(()=>window.__rejectHide());
 await page.waitForFunction(()=>document.querySelector('.recent-save-error').textContent.includes('synthetic write failure'));
 assert.equal(await action.textContent(),'确认');
 assert.equal(await first.locator('.recent-undo').isEnabled(),true);
 assert.equal(await page.locator('#recent-count').textContent(),'2');
 await action.click();
 await first.waitFor({state:'detached'});
 assert.deepEqual(await mutations(page),['101','101']);
});

test('lexicon searches each input without submit and requests only the current 20-result page',async t=>{
 const page=await openManage(t);await page.click('#lexicon-tab');
 const cards=page.locator('#lexicon-list .hint-card'), input=page.locator('#query');
 await page.waitForFunction(()=>document.querySelectorAll('#lexicon-list .hint-card').length===20);
 assert.equal(await page.locator('#lexicon-pager [data-total]').textContent(),'共 26 条');
 assert.equal(await cards.first().locator('.row-state').textContent(),'系统不提示');
 assert.equal(await cards.first().locator('.hint-rank').textContent(),'#1');
 await input.fill('o');
 await page.waitForFunction(()=>document.querySelector('#lexicon-pager [data-page]').textContent==='第 1 / 2 页' && document.querySelector('#lexicon-loading').hidden);
 assert.equal(await cards.count(),20);
 await page.locator('#lexicon-pager [data-step="1"]').click();
 await page.waitForFunction(()=>document.querySelectorAll('#lexicon-list .hint-card').length===6);
 assert.equal(await cards.last().locator('.hint-rank').textContent(),'');
 await input.fill('owor');
 await page.waitForFunction(()=>document.querySelector('#lexicon-list .hint-word').textContent==='oword');
 assert.equal(await cards.count(),1);
 await input.fill('neverfound');
 await page.waitForFunction(()=>document.querySelector('#lexicon-empty').hidden===false);
 assert.match(await page.locator('#lexicon-empty').textContent(),/没有查到/);
 const calls=await page.evaluate(()=>window.__state.calls.filter(x=>x.type==='local-lexicon'));
 assert.deepEqual(calls.map(x=>[x.query,x.sort,x.page,x.pageSize]),[['','frequency',1,20],['o','frequency',1,20],['o','frequency',2,20],['owor','frequency',1,20],['neverfound','frequency',1,20]]);
});

for (const width of [1120,320]) {
 test(`all vocabulary tabs use the same rank and word layout at ${width}px`,async t=>{
  const hidden={entryId:'701',lemma:'request',gloss:'请求',frequencyRank:1234};
  const fixture={...initial,hidden:[hidden],lexicon:[{...hidden,hintState:'eligible'}]};
  const page=await openManage(t,{fixture,hash:"#tab=42&page=receipt",pageOptions:{viewport:{width,height:800}}});
  assert.equal(await page.getByText('只读 · 已发布词库',{exact:true}).count(),0);
  for (const [tab,list,word,rank] of [
   ["recent","recent","tarnish","#2902"],["current-video","current-video","tarnish","#2902"],
   ["hidden","hidden","request","#1234"],["lexicon","lexicon","request","#1234"]
  ]) {
   await page.click(`#${tab}-tab`);
   const row=page.locator(`#${list}-list .hint-card`).filter({has:page.getByText(word,{exact:true})});
   await row.waitFor();
   assert.equal(await row.locator('.hint-rank').textContent(),rank);
   assert.deepEqual(await row.evaluate(el=>{
    const box=selector=>el.querySelector(selector).getBoundingClientRect();
    const english=box(".hint-word"),gloss=box(".hint-gloss"),card=el.getBoundingClientRect();
    return [box(".hint-rank").width,english.x-card.x,gloss.x>=english.right,
     Math.abs(english.y-gloss.y)<english.height && gloss.y<english.bottom,
     getComputedStyle(el.parentElement).columnCount,el.scrollWidth===el.clientWidth];
   }),[60,80,true,true,width===320?"1":"2",true]);
  }
  assert.equal(await page.locator("#lexicon-list .row-state").textContent(),"已停止提示");
  await page.click("#hidden-tab");
  await page.getByRole("button",{name:"恢复提示「request」"}).click();
  await page.locator("#hidden-list .hint-card").waitFor({state:"detached"});
  await page.click("#lexicon-tab");
  await page.waitForFunction(()=>document.querySelector("#lexicon-list .row-state")?.textContent==="可提示");
  const controls=await page.locator(".select-field").evaluateAll(fields=>fields.map(field=>{
   const select=getComputedStyle(field.querySelector("select")), arrow=getComputedStyle(field,"::after");
   return [select.appearance,select.paddingInlineEnd,arrow.insetInlineEnd,arrow.pointerEvents];
  }));
  assert.deepEqual(controls,Array.from({length:3},()=>["none","34px","14px","none"]));
 });
}

test('lexicon keeps just the latest queued input and does not display an obsolete response',async t=>{
 const page=await openManage(t);
 await page.evaluate(()=>{
  const send=window.chrome.runtime.sendMessage;
  window.chrome.runtime.sendMessage=m=>m.type==='local-lexicon'&&m.query==='w'
   ? new Promise(resolve=>{window.__finishLex=()=>resolve(send(m));}) : send(m);
 });
 await page.click('#lexicon-tab');
 const query=page.locator('#query');
 await page.locator('#lexicon-list .hint-card').first().waitFor();
 await query.fill('w');
 await page.waitForFunction(()=>typeof window.__finishLex==='function');
 await query.fill('wo');
 await query.fill('pword');
 assert.equal(await page.locator('#lexicon-list').isHidden(),true);
 await page.evaluate(()=>window.__finishLex());
 await page.waitForFunction(()=>document.querySelector('#lexicon-list .hint-word').textContent==='pword');
 const calls=await page.evaluate(()=>window.__state.calls.filter(x=>x.type==='local-lexicon').map(x=>x.query));
 assert.deepEqual(calls,['','w','pword']);
});

test('a lexicon service failure does not claim that a word was absent',async t=>{
 const page=await openManage(t);
 await page.evaluate(()=>{const send=window.chrome.runtime.sendMessage;window.chrome.runtime.sendMessage=m=>m.type==='local-lexicon'?Promise.resolve({ok:false,reason:'network'}):send(m);});
 await page.click('#lexicon-tab');await page.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('词库查询失败'));
 assert.equal(await page.locator('#lexicon-empty').isHidden(),true);
 assert.equal(await page.locator('#lexicon-pager').isHidden(),true);
});


test('narrow lexicon pagination keeps large totals and page buttons readable',async t=>{
 const page=await openManage(t,{lexiconTotal:265371});
 await page.setViewportSize({width:320,height:844});
 await page.click('#lexicon-tab');
 await page.waitForFunction(()=>document.querySelector('#lexicon-pager [data-total]').textContent.includes('265371'));
 const geometry=await page.locator('#lexicon-pager').evaluate(nav=>({
  overflow:document.documentElement.scrollWidth>innerWidth,
  buttons:[...nav.querySelectorAll('button')].map(button=>{
   const range=document.createRange();range.selectNodeContents(button);
   return {lines:range.getClientRects().length,height:button.getBoundingClientRect().height};
  })
 }));
 assert.equal(geometry.overflow,false);
 for(const button of geometry.buttons){assert.equal(button.lines,1);assert.ok(button.height<=48);}
});


test('recent uses two columns in rank order and fits 40 cards on a page with one compact action per card', async t => {
 const fixture = { recent: Array.from({ length: 46 }, (_, i) => ({
  entryId: String(1000 + i), lemma: i ? `example-${i + 1}` : 'a fairly long synthetic example used to check wrapping',
  gloss: i ? '合成释义' : '这是一条用于检查窄屏正常换行的合成释义', count: 46 - i
 })), hidden: [] };
 const page = await openManage(t,{fixture,pageOptions:{viewport:{width:1120,height:800}}});
 const cards = page.locator('#recent-list .recent-card');
 assert.equal(await cards.count(),40);
 assert.equal(await page.locator('#recent-list [role=columnheader]').count(),0);
 assert.equal(await page.locator('#recent-pager [data-page]').textContent(),'第 1 / 2 页');
 const bounds=await cards.evaluateAll(items=>items.map(item=>item.getBoundingClientRect().toJSON()));
 const [first,second]=bounds;
 const split=bounds.findIndex(item=>item.x>first.x);
 assert.ok(split>=19 && split<=21);
 const right=bounds[split];
 assert.equal(first.y,right.y);
 assert.ok(right.x>first.x+first.width);
 assert.equal(first.x,second.x);
 assert.ok(second.y>=first.y+first.height);
 assert.ok(second.height<=40);
 const action = cards.first().locator('.recent-action');
 const opacity = () => action.evaluate(el=>getComputedStyle(el).opacity);
 assert.equal(await opacity(),'0');
 await cards.first().locator('.hint-word').hover();
 await action.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
 assert.equal(await opacity(),'1');
 const metrics = await cards.first().evaluate(el=>({
  width:el.clientWidth, contentWidth:el.scrollWidth,
  countOpacity:getComputedStyle(el.querySelector('.hint-count')).opacity
 }));
 assert.equal(metrics.contentWidth,metrics.width);
 assert.equal(metrics.countOpacity,'0');
 await action.click();
 await action.click();
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='45');
 assert.equal(await cards.count(),40);
 assert.equal(await cards.first().locator('.hint-word').textContent(),'example-2');
 assert.equal(await cards.last().locator('.hint-word').textContent(),'example-41');
 await page.locator('#recent-pager [data-step="1"]').click();
 assert.equal(await cards.count(),5);
 assert.equal(await cards.first().locator('.hint-word').textContent(),'example-42');
});

test('keyboard focus reveals the same single-item action and retains focus after saving', async t => {
 const page = await openManage(t);
 const rows = page.locator('#recent-list .recent-card');
 await page.mouse.move(0,0);
 await page.locator('#connection-tab').focus();
 await page.keyboard.press('Tab');
 assert.equal(await page.locator('#recent-query').evaluate(el=>el===document.activeElement),true);
 await page.keyboard.press('Tab');
 assert.equal(await page.locator('#recent-page-size').evaluate(el=>el===document.activeElement),true);
 await page.keyboard.press('Tab');
 const first = rows.first().locator('.recent-action');
 assert.equal(await first.evaluate(el=>el===document.activeElement),true);
 await first.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
 assert.equal(await first.evaluate(el=>getComputedStyle(el).opacity),'1');
 await page.keyboard.press('Enter');
 await page.keyboard.press('Shift+Tab');
 assert.equal(await card(page,'101').locator('.recent-undo').evaluate(el=>el===document.activeElement),true);
 await page.keyboard.press('Space');
 assert.equal(await first.evaluate(el=>el===document.activeElement),true);
 assert.deepEqual(await mutations(page),[]);
 await page.keyboard.press('Enter');
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.activeElement?.dataset.entry==='102');
 assert.equal(await card(page,'101').count(),0);
 assert.deepEqual(await mutations(page),['101']);
});

test('narrow touch screen falls back to one column with a tappable action and no overflow', async t => {
 const page = await openManage(t,{pageOptions:{hasTouch:true,isMobile:true,viewport:{width:320,height:720}}});
 const cards = page.locator('#recent-list .recent-card');
 const [first,second] = await Promise.all([cards.nth(0).boundingBox(),cards.nth(1).boundingBox()]);
 assert.equal(first.x,second.x);
 assert.ok(second.y>=first.y+first.height);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth),true);
 const action = cards.first().locator('.recent-action');
 await action.scrollIntoViewIfNeeded();
 assert.equal(await action.evaluate(el=>getComputedStyle(el).opacity),'1');
 assert.equal(await action.evaluate(el=>el.getBoundingClientRect().height>=44),true);
 const anchor=await action.boundingBox(), x=anchor.x+anchor.width/2, y=anchor.y+anchor.height/2;
 await page.touchscreen.tap(x,y);
 assert.deepEqual(await action.boundingBox(),anchor);
 assert.equal(await cards.first().locator('.recent-undo').evaluate(el=>el.getBoundingClientRect().height>=44),true);
 await page.touchscreen.tap(x,y);
 await page.waitForFunction(()=>document.querySelector('#hidden-count').textContent==='2');
 assert.deepEqual(await mutations(page),['101']);
});


test("recent filters locally before paging and restores only the explicit page size",async t=>{
 const recent=Array.from({length:92},(_,i)=>({
   entryId:String(300+i),lemma:i===91?"literal [tag]":i%2===0?`Target item ${i}`:`Other item ${i}`,
   gloss:i===90?"过滤样例":`释义 ${i}`,count:100-i
 }));
 const page=await openManage(t,{fixture:{recent,hidden:[recent[0]]},clock:true});
 const rows=page.locator("#recent-list .recent-card"), query=page.locator("#recent-query"),
   size=page.locator("#recent-page-size"), pager=page.locator("#recent-pager");
 const pageLabel=()=>pager.locator("[data-page]").textContent();
 const firstWord=()=>rows.first().locator(".hint-word").textContent();
 const next=()=>pager.locator('[data-step="1"]').click();
 assert.equal(await size.inputValue(),"40");
 assert.equal(await rows.count(),40);
 await next();
 assert.equal(await pageLabel(),"第 2 / 3 页");
 await size.selectOption("20");
 assert.equal(await rows.count(),20);
 assert.equal(await pageLabel(),"第 1 / 5 页");
 await next();
 const calls=await page.evaluate(()=>window.__state.calls);
 await query.fill("  ＴＡＲＧＥＴ  ");
 assert.equal(await pageLabel(),"第 1 / 3 页");
 assert.equal(await firstWord(),"Target item 2");
 assert.equal(await rows.count(),20);
 assert.equal(await pager.locator("[data-total]").textContent(),"共 45 条");
 assert.equal(await page.locator("#recent-count").textContent(),"91");
 await next();
 assert.equal(await firstWord(),"Target item 42");
 await query.fill("过滤");
 assert.equal(await rows.count(),1);
 assert.equal(await firstWord(),"Target item 90");
 await query.fill("[");
 assert.equal(await firstWord(),"literal [tag]");
 await query.fill("not found");
 assert.equal(await rows.count(),0);
 assert.match(await page.locator("#recent-empty").textContent(),/没有匹配/);
 assert.equal(await pager.isHidden(),true);
 assert.deepEqual(await page.evaluate(()=>window.__state.calls),calls);
 await query.fill("");
 assert.equal(await rows.count(),20);
 await size.selectOption("80");
 assert.equal(await rows.count(),80);
 const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem("fixture:manage")));
 assert.deepEqual(saved,{recentHintsPageSize:80});
 await query.fill("[");
 await page.reload();
 await page.waitForFunction(()=>document.querySelectorAll("#recent-list .recent-card").length===80);
 assert.equal(await query.inputValue(),"");
 assert.equal(await size.inputValue(),"80");
});

test("recent controls remain inside a narrow management tab",async t=>{
 const page=await openManage(t,{pageOptions:{viewport:{width:320,height:844}}});
 assert.equal(await page.locator("#recent-query").isVisible(),true);
 assert.equal(await page.locator("#recent-page-size").isVisible(),true);
 const bounds=await page.locator(".recent-filters").evaluate(el=>({
   overflow:el.scrollWidth>el.clientWidth, documentOverflow:document.documentElement.scrollWidth>innerWidth,
   clipped:[...el.querySelectorAll("input,select")].some(x=>x.getBoundingClientRect().right>innerWidth)
 }));
 assert.deepEqual(bounds,{overflow:false,documentOverflow:false,clipped:false});
});

test('management shows the bound video and its counts, then clears stale source without using another tab',async t=>{
 const page=await openManage(t,{hash:"#tab=42&page=receipt",clock:true});
 const link=page.locator("#current-video-link");
 await page.click("#current-video-tab");
 await link.waitFor({state:"visible"});
 assert.equal(await link.getAttribute("href"),"https://www.youtube.com/watch?v=LfxE2ETest1");
 assert.match(await link.textContent(),/LfxE2ETest1/u);
 const current=page.locator("#current-video-list .hint-card");
 assert.deepEqual(await current.locator(".hint-word").allTextContents(),["protect","tarnish","zero"]);
 assert.deepEqual(await current.locator(".hint-rank").allTextContents(),["","#2902","#24"]);
 assert.deepEqual(await current.locator(".hint-count").allTextContents(),["3 次","1 次","0 次"]);
 assert.equal(await current.locator("button").count(),0);
 await page.click("#refresh-current-video");
 await page.waitForFunction(()=>window.__state.calls.filter(x=>x.type==="page-vocabulary").length===2);
 assert.deepEqual(await page.evaluate(()=>window.__state.calls.filter(x=>x.type==="page-vocabulary")),
   Array(2).fill({tab:42,type:"page-vocabulary",pageKey:"receipt"}));
 await page.evaluate(()=>{window.__state.videoAvailable=false;});
 await page.clock.runFor(2000);
 await page.waitForFunction(()=>document.querySelector("#refresh-current-video").disabled);
 assert.equal(await current.count(),0);
 assert.equal(await link.isHidden(),true);
 assert.equal(await link.getAttribute("href"),null);
 assert.equal(await link.textContent(),"");
 assert.match(await page.locator("#current-video-status").textContent(),/原视频已切换或关闭/u);
});

test("the video title replaces a plain URL when it arrives, without becoming markup",async t=>{
 const page=await openManage(t,{hash:"#tab=42&page=receipt"});
 const link=page.locator("#current-video-link");
 await page.click("#current-video-tab");
 await link.waitFor({state:"visible"});
 assert.equal(await link.textContent(),"youtube.com/watch?v=LfxE2ETest1");
 await page.evaluate(()=>{window.__state.videoTitle=" \n 合成标题 <img src=x> ";});
 await page.click("#refresh-current-video");
 await page.waitForFunction(()=>document.querySelector("#current-video-link").textContent==="合成标题 <img src=x>");
 assert.equal(await link.locator("img").count(),0);
 assert.equal(await link.getAttribute("href"),"https://www.youtube.com/watch?v=LfxE2ETest1");
});

test("only panel reading guidance uses the quote-like border",async t=>{
 const page=await openManage(t);
 for (const name of ["current-video","recent","hidden","lexicon"]) {
  await page.click(`#${name}-tab`);
  const note=page.locator(`#${name}-panel > .reading-note`);
  assert.equal(await note.count(),1);
  assert.equal(await note.evaluate(el=>getComputedStyle(el).borderInlineStartStyle),"solid");
 }
 assert.equal(await page.locator("#cutoff-source-note.reading-note, #cutoff-status.reading-note, #connection-note.reading-note, [role=status].reading-note").count(),0);
});

test('both lists place Chinese on the right of English at wide and narrow widths',async t=>{
 const page=await openManage(t,{hash:"#tab=42&page=receipt"});
 const geometry=(selector,word)=>page.locator(selector).locator(".hint-card").filter({hasText:word}).evaluate(card=>{
  const box=el=>el.getBoundingClientRect();
  const [rank,english,gloss,count]=[".hint-rank",".hint-word",".hint-gloss",".hint-count"].map(s=>box(card.querySelector(s)));
  return {rankWidth:rank.width,wordOffset:english.x-box(card).x,glossRight:gloss.x>=english.right,
   sameLine:gloss.top<english.bottom && gloss.bottom>english.top,countInset:box(card).right-count.right,
   overflow:card.scrollWidth>card.clientWidth};
 });
 for (const width of [1120,320]) {
  await page.setViewportSize({width,height:800});
  await page.click("#recent-tab");
  const recent=await geometry("#recent-list","tarnish");
  await page.click("#current-video-tab");
  const current=await geometry("#current-video-list","tarnish");
  assert.deepEqual(current,recent);
  assert.deepEqual(current,{rankWidth:60,wordOffset:80,glossRight:true,sameLine:true,countInset:12,overflow:false});
 }
});

test("hidden and lexicon entries reuse compact cards with their own right-side controls",async t=>{
 const page=await openManage(t);
 for (const width of [1120,320]) {
  await page.setViewportSize({width,height:800});
  await page.click("#hidden-tab");
  const hidden=page.locator("#hidden-list .hint-card").first();
  assert.equal(await hidden.locator(".hint-word").textContent(),"settlement");
  assert.ok(await hidden.locator(".hint-rail .restore-action").isVisible());
  await page.click("#lexicon-tab");
  const entry=page.locator("#lexicon-list .hint-card").first();
  await entry.waitFor({state:"visible"});
  assert.equal(await entry.locator(".hint-rail .row-state").textContent(),"系统不提示");
  for (const row of [hidden,entry]) {
   if (row===hidden) await page.click("#hidden-tab");
   else {
    await page.click("#lexicon-tab");
    await entry.waitFor({state:"visible"});
   }
   assert.ok(await row.evaluate(el=>{
    const en=el.querySelector(".hint-word").getBoundingClientRect(), zh=el.querySelector(".hint-gloss").getBoundingClientRect();
    return zh.x>=en.right && zh.top<en.bottom && el.scrollWidth===el.clientWidth;
   }));
  }
 }
});

test("long terms wrap without clipping in either list at 320px",async t=>{
 const term="fixture".repeat(45), gloss="合成释义".repeat(12);
 const page=await openManage(t,{hash:"#tab=42&page=receipt",pageOptions:{viewport:{width:320,height:850}},
  fixture:{hidden:[],recent:[{entryId:"201",lemma:term,gloss,frequencyRank:1234,count:2}]}});
 await page.evaluate(([english,chinese])=>{window.__state.videoRows=[{english,chinese,frequencyRank:1234,hintCount:2}];},[term,gloss]);
 for (const name of ["recent","current-video"]) {
  await page.click(`#${name}-tab`);
  const entry=page.locator(`#${name}-list .hint-term`);
  await entry.waitFor({state:"visible"});
  assert.equal(await entry.locator(".hint-gloss").textContent(),gloss);
  assert.deepEqual(await entry.evaluate(el=>({
   wrapped:el.querySelector(".hint-word").getClientRects().length>1,
   clipped:el.scrollWidth>el.clientWidth||document.documentElement.scrollWidth>innerWidth
  })),{wrapped:true,clipped:false});
 }
});

test('real content retains rendered hints while disabled and invalidates the old management session on navigation',async t=>{
 const instance=await (browser??=chromium.launch({headless:true,...(chromePath?{executablePath:chromePath}:{})}));
 const video=await instance.newPage();
 video.setDefaultTimeout(8_000);
 t.after(()=>video.context().close());
 await video.route("https://lexiflow-watch.test/**",route=>route.fulfill({contentType:"text/html",body:`<!doctype html>
  <div id="movie_player" class="html5-video-player" style="position:relative;width:700px;height:420px">
    <video></video>
    <div id="ytp-caption-window-container" style="position:absolute;bottom:40px;left:20px;font-size:24px">
      <span class="ytp-caption-segment">A reliable result.</span>
    </div>
  </div>`}));
 await video.goto("https://lexiflow-watch.test/watch?v=LfxE2ETest1");
 await video.evaluate(()=>{
  const handlers=[];
  const id="fixture-extension",manage=`chrome-extension://${id}/manage.html`;
  const state={requests:0,presented:0};window.__video=state;
  window.__videoData={video_id:"LfxE2ETest1",title:"  合成视频甲  "};
  document.querySelector("#movie_player").getVideoData=()=>window.__videoData;
  window.__cue=text=>{
   document.querySelector(".ytp-caption-segment").textContent=text;
   document.querySelector("video").dispatchEvent(new Event("timeupdate",{bubbles:true}));
  };
  window.__message=message=>{
   let reply;
   const url=message.type==="page-vocabulary"?`${manage}#tab=42&page=${message.pageKey}`:`chrome-extension://${id}/popup.html`;
   for(const handler of handlers)handler(message,{id,url},value=>{reply=value;});
   return reply;
  };
  (window.chrome??={}).runtime={
   id,getURL:name=>`chrome-extension://${id}/${name}`,onMessage:{addListener:fn=>handlers.push(fn)},
   async sendMessage(message){
    if(message.type==="local-preferences")return {ok:true,commonWordCutoff:1500,hidden:[]};
    if(message.type==="local-presented"){state.presented+=message.entryIds.length;return {ok:true};}
    if(message.type!=="caption-hints")return {ok:true};
    state.requests++;
    const segments=message.payload.currentSnapshot.captions.flatMap(group=>group.segments).filter(segment=>segment.append);
    const target=segments.find(segment=>segment.text.includes("reliable"));
    const startOffset=target?.text.indexOf("reliable")??-1;
    return {ok:true,body:{lexiconVersion:1,processedKeys:segments.map(segment=>segment.key),
     hints:target?[{startKey:target.key,endKey:target.key,startOffset,endOffset:startOffset+8,
      chineseGloss:"可靠的",lexiconEntryId:"100",frequencyRank:2902}]:[]}};
   }
  };
 });
 await video.addScriptTag({path:resolve(dist,"youtube-bridge.js")});
 await video.addScriptTag({path:resolve(dist,"content.js")});
 await video.locator("#lexiflow-caption-overlay .gloss").waitFor({state:"visible"});
 const send=message=>video.evaluate(message=>window.__message(message),message);
 const {pageKey}=await send({type:"page-enhancement",action:"read"});
 const before=await video.evaluate(()=>({...window.__video}));
 assert.ok(before.presented>0);
 const off=await send({type:"page-enhancement",action:"set",pageKey,enabled:false});
 assert.equal(off.enabled,false);
 assert.equal(off.pageKey,pageKey);
 await video.evaluate(()=>window.__cue("Another reliable result."));
 await video.locator("#lexiflow-caption-overlay .gloss").waitFor({state:"hidden"});

 const page=await openManage(t,{hash:`#tab=42&page=${pageKey}`});
 await page.exposeFunction("__readVideo",(tab,message)=>tab===42?send(message):{ok:false});
 await page.evaluate(()=>{window.chrome.tabs.sendMessage=window.__readVideo;});
 await page.click("#current-video-tab");
 const word=page.locator("#current-video-list .hint-word"),count=page.locator("#current-video-list .hint-count");
 await word.waitFor({state:"visible"});
 assert.equal(await word.textContent(),"reliable");
 assert.equal(await count.textContent(),"1 次");
 assert.equal(await page.locator("#current-video-link").textContent(),"合成视频甲");
 assert.deepEqual(await video.evaluate(()=>({...window.__video})),before);

 await video.evaluate(()=>{
  document.dispatchEvent(new Event("yt-navigate-start"));
  history.pushState({},"","/watch?v=LfxE2ENext1");
  window.__cue("");
  document.dispatchEvent(new Event("yt-navigate-finish"));
 });
 await page.click("#refresh-current-video");
 await word.waitFor({state:"hidden"});
 assert.equal(await page.locator("#current-video-link").isHidden(),true);
 const next=await send({type:"page-enhancement",action:"read"});
 assert.notEqual(next.pageKey,pageKey);
 assert.deepEqual(await send({type:"page-vocabulary",pageKey:next.pageKey}),
  {ok:true,videoId:"LfxE2ENext1",pageKey:next.pageKey,rows:[]});
 await video.evaluate(()=>{
  document.title="不得借用的旧页面标题";
  window.__videoData={video_id:"LfxE2ENext1",title:"合成视频乙"};
 });
 assert.deepEqual(await send({type:"page-vocabulary",pageKey:next.pageKey}),
  {ok:true,videoId:"LfxE2ENext1",pageKey:next.pageKey,title:"合成视频乙",rows:[]});
 await video.evaluate(()=>history.pushState({},"","/?v=LfxE2ENext1"));
 assert.deepEqual(await send({type:"page-vocabulary",pageKey:next.pageKey}),{ok:false,reason:"not-video-page"});
});

test('direct management open does not guess a video; recent rankings preserve layout and counts',async t=>{
 const page=await openManage(t);
 assert.deepEqual(await page.locator(".recent-card .hint-rank").allTextContents(),["#2902",""]);
 assert.deepEqual(await page.locator(".recent-card .hint-count").allTextContents(),["6 次","4 次"]);
 const geometry=await page.locator(".recent-card").evaluateAll(cards=>cards.map(card=>({
   left:getComputedStyle(card).paddingLeft,right:getComputedStyle(card).paddingRight,
   rankWidth:card.querySelector(".hint-rank").getBoundingClientRect().width
 })));
 assert.deepEqual(geometry,[{left:"12px",right:"12px",rankWidth:60},{left:"12px",right:"12px",rankWidth:60}]);
 await page.click("#current-video-tab");
 assert.equal(await page.locator("#refresh-current-video").isDisabled(),true);
 assert.equal(await page.evaluate(()=>window.__state.calls.some(x=>x.type==="page-vocabulary")),false);
});

test('connection edit and copy stay on the management page and failure keeps the old route',async t=>{
 const page=await openManage(t);
 await page.click("#connection-tab");
 const input=page.locator("#connection-url");
 await page.waitForFunction(()=>document.querySelector("#connection-url").value==="http://127.0.0.1:19081");
 const submit=page.locator("#save-connection");
 await input.fill("http://remote.example:19081"); await submit.click();
 assert.equal(await input.getAttribute("aria-invalid"),"true");
 await page.evaluate(()=>{window.__state.failConnection=true;});
 await input.fill("http://localhost:19082"); await submit.click();
 assert.match(await page.locator("#connection-status").textContent(),/版本不匹配/u);
 assert.match(await page.locator("#connection-target").textContent(),/19081/u);
 assert.equal(await input.inputValue(),"http://localhost:19082");
 await page.evaluate(()=>{window.__state.failConnection=false;});
 await submit.click();
 await page.waitForFunction(()=>document.querySelector("#connection-status").textContent==="已保存连接。");
 assert.equal(await input.isVisible(),true);
 assert.equal(await page.evaluate(()=>window.__state.copied),undefined);
 assert.equal(await page.locator("#connection-identity").textContent(),"服务版本：3.0.0 · 开发版");
 await page.locator("#copy-diagnostics").click();
 const copied=await page.evaluate(()=>window.__state.copied);
 assert.match(copied,/3\.0\.0-SNAPSHOT/u);
 assert.match(copied,/caption-hints.v5/u);
 assert.match(copied,/localhost:19082/u);
 assert.doesNotMatch(copied,/fixture-secret|pageKey/u);
});

test('a failed or overdue diagnostic copy releases the button, ignores a late receipt, and allows retry',async t=>{
 const page=await openManage(t,{clock:true});
 await page.click("#connection-tab");
 const target=page.locator("#connection-target");
 const copy=page.locator("#copy-diagnostics");
 const status=page.locator("#copy-status");
 const ready=await target.textContent();
 await page.evaluate(()=>{window.__state.copyOperation="denied";});
 await copy.click();
 await page.waitForFunction(()=>document.querySelector("#copy-status").textContent.includes("复制失败"));
 assert.equal(await copy.isEnabled(),true);
 assert.equal(await target.textContent(),ready);
 await page.evaluate(()=>{window.__state.copyOperation="hold";});
 await copy.click();
 assert.equal(await copy.isDisabled(),true);
 await page.clock.runFor(5001);
 assert.equal(await copy.isEnabled(),true);
 await page.evaluate(()=>window.__state.finishCopy());
 assert.match(await status.textContent(),/复制失败/u);
 await page.evaluate(()=>{window.__state.copyOperation="";});
 await copy.click();
 await page.waitForFunction(()=>document.querySelector("#copy-status").textContent.includes("已复制"));
 assert.equal(await target.textContent(),ready);
});
