import assert from 'node:assert/strict';
import test, {after} from 'node:test';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {chromium} from 'playwright';

const dist=resolve(import.meta.dirname,'../dist');
const chromePath=process.env.LEXIFLOW_CHROME_EXECUTABLE || (process.platform==='darwin'&&existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined);
const initial={recent:[{entryId:'101',lemma:'tarnish',gloss:'失泽',frequencyRank:2902,count:6},{entryId:'102',lemma:'protect',gloss:'保护',frequencyRank:null,count:4}],hidden:[{entryId:'103',lemma:'settlement',gloss:'安顿'}]};
let browser;
after(async()=>{if(browser)await (await browser).close();});
async function openManage(t,{preferencesAvailable=true,clock=false,lexiconTotal,fixture=initial,pageOptions,hash=""}={}) {
 const instance=await (browser??=chromium.launch({headless:true,...(chromePath?{executablePath:chromePath}:{})}));
 const page=await instance.newPage(pageOptions);page.setDefaultTimeout(8_000);t.after(()=>page.context().close());
 if(clock) await page.clock.install({time:new Date('2026-11-10T00:00:10Z')});
 await page.addInitScript(({initial,preferencesAvailable,lexiconTotal})=>{
  const s={recent:structuredClone(initial.recent),hidden:structuredClone(initial.hidden),cutoff:1500,calls:[],lexPageSize:20,videoAvailable:true,backend:"http://127.0.0.1:19081"};window.__state=s;
  const pref=()=>({ok:true,commonWordCutoff:s.cutoff,hidden:structuredClone(s.hidden)});
  const allLex=Array.from({length:26},(_,i)=>({entryId:String(200+i),lemma:String.fromCharCode(97+i)+'word',gloss:`释义 ${i}`,frequencyRank:i===25?null:i+1,hintState:i%4===0?'blocked':'eligible'}));
  const storage=()=>JSON.parse(localStorage.getItem("fixture:manage")??"{}");
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{
   if(s.copyOperation==="denied") throw new Error("fixture denied");
   if(s.copyOperation==="hold") await new Promise(resolve=>{s.finishCopy=resolve;});
   s.copied=text;
  }}});
  const runtime={softwareVersion:"3.0.0-SNAPSHOT.20261121.gcac57bd.dirty.123456789abc",apiContract:"caption-hints.v4",mode:"demo",ready:false,reason:"DEMO_MODE",datasetVersion:null,
     publishedDataset:null,privateInput:"fixture-secret"};
  window.chrome={tabs:{async sendMessage(tab,message){s.calls.push({tab,...message});
    return s.videoAvailable&&tab===42&&message.pageKey==="receipt"
     ? {ok:true,pageKey:"receipt",rows:[{english:"zero",chinese:"零",frequencyRank:24,hintCount:0},{english:"missing",chinese:"无名次",frequencyRank:null,hintCount:2}]}
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
   if(m.type==='local-preferences'&&m.action==='hide'){const x=[...s.recent,...allLex].find(item=>item.entryId===m.entryId);if(x&&!s.hidden.some(h=>h.entryId===m.entryId))s.hidden.push({entryId:x.entryId,lemma:x.lemma,gloss:x.gloss});return pref();}
   if(m.type==='local-preferences'&&m.action==='restore'){s.hidden=s.hidden.filter(x=>x.entryId!==m.entryId);return pref();}
   if(m.type==='local-lexicon'){const q=m.query.toLowerCase();const found=allLex.filter(x=>!q||x.lemma===q);found.sort((a,b)=>m.sort==='alphabet'?a.lemma.localeCompare(b.lemma):(a.frequencyRank??Infinity)-(b.frequencyRank??Infinity));const start=(m.page-1)*m.pageSize;return {ok:true,items:found.slice(start,start+m.pageSize),total:lexiconTotal??found.length,page:m.page,pageSize:m.pageSize};}
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
 assert.equal(await first.locator('.recent-count').textContent(),'6 次');
 await first.locator('.recent-word').hover();
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
 assert.equal(await page.locator('#hidden-list .row:not(.head)').count(),2);
 await page.locator('#hidden-list .row').filter({hasText:'tarnish'}).getByRole('button',{name:'恢复提示'}).click();
 await page.waitForFunction(()=>document.querySelector('#hidden-count').textContent==='1');
 assert.match(await page.locator('#feedback').textContent(),/已恢复/);
 await page.click('#recent-tab');
 assert.deepEqual(await page.locator('#recent-list .recent-word').allTextContents(),['tarnish','protect']);
});

test('first click swaps the count for equal left Undo and fixed right Confirm; undo does not pin the action',async t=>{
 const page=await openManage(t);
 const first=card(page,'101'), second=card(page,'102');
 assert.equal(await page.locator('#recent-panel input[type=checkbox], #hide-selected').count(),0);
 const action=first.locator('.recent-action'), undo=first.locator('.recent-undo');
 await first.locator('.recent-word').hover();
 await action.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
 const anchor=await action.boundingBox();
 await page.mouse.click(anchor.x+anchor.width/2,anchor.y+anchor.height/2);
 assert.deepEqual(await mutations(page),[]);
 assert.equal(await action.textContent(),'确认');
 assert.equal(await undo.textContent(),'撤销');
 assert.equal(await first.locator('.recent-count').isHidden(),true);
 assert.deepEqual(await action.boundingBox(),anchor);
 const left=await undo.boundingBox();
 assert.deepEqual([left.width,left.height,left.y],[anchor.width,anchor.height,anchor.y]);
 assert.ok(left.x+left.width<anchor.x);
 await undo.click();
 assert.equal(await undo.isHidden(),true);
 await second.locator('.recent-word').hover();
 await page.waitForFunction(()=>getComputedStyle(document.querySelector('.recent-card[data-entry="101"] .recent-action')).opacity==='0');
 assert.equal(await first.locator('.recent-count').isVisible(),true);
 assert.deepEqual(await mutations(page),[]);
 await second.getByRole('button',{name:'不再提示「protect」'}).click();
 await first.locator('.recent-word').hover();
 await action.click();
 assert.equal(await second.getAttribute('data-pending'),null);
 assert.equal(await first.getAttribute('data-pending'),'');
 assert.deepEqual(await mutations(page),[]);
});

test('recent shows only the seven-day ranking and refreshes on the minute and on return',async t=>{
 const page=await openManage(t,{clock:true});
 const rows=page.locator('#recent-list .recent-card');
 const names=()=>rows.locator('.recent-word').allTextContents();
 assert.equal(await page.locator('#recent-heading').textContent(),'近 7 天提示');
 assert.equal(await page.locator('#recent-page-size').inputValue(),'40');
 assert.deepEqual(await names(),['tarnish','protect']);
 await page.evaluate(()=>{window.__state.recent.shift();window.__state.recent[0].count=7;});
 await page.clock.fastForward(60_000);
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='1');
 assert.deepEqual(await names(),['protect']);
 assert.equal(await rows.locator('.recent-count').textContent(),'7 次');

 // A pending confirmation stays stable; leaving the tab and hiding the document stop the minute reads.
 await card(page,'102').locator('.recent-word').hover();
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

test('cutoff is managed here, saves with feedback, and restores the confirmed choice on failure',async t=>{
 const page=await openManage(t);
 const select=page.getByLabel('不提示 BNC 高频前 N 词', { exact: true });
 assert.equal(await page.locator('#cutoff-source-note').textContent(),'BNC 是英国国家语料库，不按列表里的 # 名次过滤。');
 await page.waitForFunction(()=>document.querySelector('#cutoff-status').dataset.kind==='success');
 assert.equal(await select.inputValue(),'1500');await select.selectOption('2000');
 await page.waitForFunction(()=>document.querySelector('#cutoff-status').textContent.includes('已保存：Top 2000'));
 assert.equal(await select.inputValue(),'2000');
 await page.evaluate(()=>{const send=window.chrome.runtime.sendMessage;window.chrome.runtime.sendMessage=async m=>m.type==='local-preferences'&&m.action==='setCutoff'?{ok:false,reason:'network'}:send(m);});
 await select.selectOption('1000');await page.waitForFunction(()=>document.querySelector('#cutoff-status').textContent.includes('保存失败'));
 assert.equal(await select.inputValue(),'2000');
});

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
 await first.locator('.recent-word').hover();
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

test('lexicon uses server paging and sorting, displays null rank and an empty exact search',async t=>{
 const page=await openManage(t);await page.click('#lexicon-tab');await page.waitForFunction(()=>document.querySelectorAll('#lexicon-list .row:not(.head)').length===20);
 assert.equal(await page.locator('#lexicon-pager [data-total]').textContent(),'共 26 条');assert.equal(await page.locator('#lexicon-list .row').nth(1).locator('.row-state').textContent(),'系统不提示');
 await page.locator('#lexicon-pager [data-step="1"]').click();await page.waitForFunction(()=>document.querySelectorAll('#lexicon-list .row:not(.head)').length===6);
 assert.equal(await page.locator('#lexicon-list').getByText('暂无').count(),1);
 await page.locator('#query').fill('neverfound');await page.locator('#lexicon-search button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#lexicon-empty').hidden===false);
 assert.match(await page.locator('#lexicon-empty').textContent(),/没有查到/);
 const calls=await page.evaluate(()=>window.__state.calls.filter(x=>x.type==='local-lexicon'));
 assert.deepEqual(calls.map(x=>[x.query,x.sort,x.page,x.pageSize]),[['','frequency',1,20],['','frequency',2,20],['neverfound','frequency',1,20]]);
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
 const [first,second,right] = await Promise.all([0,1,20].map(i=>cards.nth(i).boundingBox()));
 assert.equal(first.y,right.y);
 assert.ok(right.x>first.x+first.width);
 assert.equal(first.x,second.x);
 assert.ok(second.y>=first.y+first.height);
 const action = cards.first().locator('.recent-action');
 const opacity = () => action.evaluate(el=>getComputedStyle(el).opacity);
 assert.equal(await opacity(),'0');
 await cards.first().locator('.recent-word').hover();
 await action.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
 assert.equal(await opacity(),'1');
 const metrics = await cards.first().evaluate(el=>({
  width:el.clientWidth, contentWidth:el.scrollWidth,
  countOpacity:getComputedStyle(el.querySelector('.recent-count')).opacity
 }));
 assert.equal(metrics.contentWidth,metrics.width);
 assert.equal(metrics.countOpacity,'0');
 await action.click();
 await action.click();
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='45');
 assert.equal(await cards.count(),40);
 assert.equal(await cards.first().locator('.recent-word').textContent(),'example-2');
 assert.equal(await cards.last().locator('.recent-word').textContent(),'example-41');
 await page.locator('#recent-pager [data-step="1"]').click();
 assert.equal(await cards.count(),5);
 assert.equal(await cards.first().locator('.recent-word').textContent(),'example-42');
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
 const firstWord=()=>rows.first().locator(".recent-word").textContent();
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

test('management shows current-video zero and absent rank, then clears stale source without using another tab',async t=>{
 const page=await openManage(t,{hash:"#tab=42&page=receipt"});
 await page.click("#current-video-tab");
 await page.waitForFunction(()=> document.querySelectorAll("#current-video-list .current-word").length===2);
 assert.deepEqual(await page.locator(".current-word .hint-rank").allTextContents(),["#24",""]);
 assert.deepEqual(await page.locator(".current-count").allTextContents(),["提示 0 次","提示 2 次"]);
 await page.click("#refresh-current-video");
 await page.waitForFunction(()=>window.__state.calls.filter(x=>x.type==="page-vocabulary").length===2);
 assert.deepEqual(await page.evaluate(()=>window.__state.calls.filter(x=>x.type==="page-vocabulary")),
   Array(2).fill({tab:42,type:"page-vocabulary",pageKey:"receipt"}));
 await page.evaluate(()=>{window.__state.videoAvailable=false;});
 await page.click("#refresh-current-video");
 await page.waitForFunction(()=>document.querySelector("#refresh-current-video").disabled);
 assert.equal(await page.locator(".current-word").count(),0);
 assert.match(await page.locator("#current-video-status").textContent(),/原视频已切换或关闭/u);
});

test('direct management open does not guess a video; recent rankings preserve layout and counts',async t=>{
 const page=await openManage(t);
 assert.deepEqual(await page.locator(".recent-card .hint-rank").allTextContents(),["#2902",""]);
 assert.deepEqual(await page.locator(".recent-card .recent-count").allTextContents(),["6 次","4 次"]);
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
 assert.match(copied,/caption-hints.v4/u);
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
