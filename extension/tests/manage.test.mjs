import assert from 'node:assert/strict';
import test from 'node:test';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
import {chromium} from 'playwright';

const dist=resolve(import.meta.dirname,'../dist');
const chromePath=process.env.LEXIFLOW_CHROME_EXECUTABLE || (process.platform==='darwin'&&existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':undefined);
const initial={recent:[{entryId:'101',lemma:'tarnish',gloss:'失泽',count:6},{entryId:'102',lemma:'protect',gloss:'保护',count:4}],hidden:[{entryId:'103',lemma:'settlement',gloss:'安顿'}]};
async function openManage(t,{preferencesAvailable=true,clock=false,lexiconTotal}={}) {
 const browser=await chromium.launch({headless:true,...(chromePath?{executablePath:chromePath}:{})});t.after(()=>browser.close());const page=await browser.newPage();
 if(clock) await page.clock.install({time:new Date('2026-11-10T00:00:10Z')});
 await page.addInitScript(({initial,preferencesAvailable,lexiconTotal})=>{
  const s={recent:structuredClone(initial.recent),hidden:structuredClone(initial.hidden),cutoff:1500,calls:[],lexPageSize:20};window.__state=s;
  const pref=()=>({ok:true,commonWordCutoff:s.cutoff,hidden:structuredClone(s.hidden)});
  const allLex=Array.from({length:26},(_,i)=>({entryId:String(200+i),lemma:String.fromCharCode(97+i)+'word',gloss:`释义 ${i}`,frequencyRank:i===25?null:i+1,hintState:i%4===0?'blocked':'eligible'}));
  window.chrome={runtime:{sendMessage:async m=>{s.calls.push(structuredClone(m));
   if(m.type==='local-preferences'&&m.action==='read')return preferencesAvailable?pref():{ok:false,reason:'network'};
   if(m.type==='local-preferences'&&m.action==='setCutoff'){s.cutoff=m.commonWordCutoff;return pref();}
   if(m.type==='local-recent')return s.recentUnavailable?{ok:false,reason:'network'}:{ok:true,items:structuredClone(s.recent).sort((a,b)=>b.count-a.count)};
   if(m.type==='local-preferences'&&m.action==='hide'){const x={entryId:m.entryId,lemma:m.lemma,gloss:m.gloss};if(!s.hidden.some(h=>h.entryId===x.entryId))s.hidden.push(x);return pref();}
   if(m.type==='local-preferences'&&m.action==='restore'){s.hidden=s.hidden.filter(x=>x.entryId!==m.entryId);return pref();}
   if(m.type==='local-lexicon'){const q=m.query.toLowerCase();const found=allLex.filter(x=>!q||x.lemma===q);found.sort((a,b)=>m.sort==='alphabet'?a.lemma.localeCompare(b.lemma):(a.frequencyRank??Infinity)-(b.frequencyRank??Infinity));const start=(m.page-1)*m.pageSize;return {ok:true,items:found.slice(start,start+m.pageSize),total:lexiconTotal??found.length,page:m.page,pageSize:m.pageSize};}
   return {ok:false,reason:'unknown-message'};
  }}};
 },{initial,preferencesAvailable,lexiconTotal});
 await page.goto('file://'+resolve(dist,'manage.html'));await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='2');return page;
}

test('management shows actual recent counts, hidden restore, and truthful local preference results',async t=>{
 const page=await openManage(t);assert.equal(await page.locator('#recent-count').textContent(),'2');
 assert.equal(await page.locator('#recent-list .row:not(.head)').count(),2);assert.equal(await page.locator('#recent-list .row').nth(1).locator('.number').textContent(),'6');
 await page.getByRole('button',{name:'不再提示'}).first().click();await page.waitForFunction(()=>document.querySelector('#hidden-count').textContent==='2');
 assert.match(await page.locator('#feedback').textContent(),/已将 1 条保存为本机隐藏偏好/);
 await page.click('#hidden-tab');assert.equal(await page.locator('#hidden-list .row:not(.head)').count(),2);
 await page.getByRole('button',{name:'恢复提示'}).first().click();await page.waitForFunction(()=>document.querySelector('#hidden-count').textContent==='1');
 assert.match(await page.locator('#feedback').textContent(),/已恢复/);
});

test('recent shows only the seven-day ranking and refreshes on the minute and on return',async t=>{
 const page=await openManage(t,{clock:true});
 const rows=page.locator('#recent-list .row:not(.head)');
 const names=()=>rows.locator('[role=cell]:nth-child(2)').allTextContents();
 assert.equal(await page.locator('#recent-heading').textContent(),'近 7 天提示');
 assert.equal(await page.locator('#recent-panel select').count(),0);
 assert.deepEqual(await names(),['tarnish','protect']);
 await page.evaluate(()=>{window.__state.recent.shift();window.__state.recent[0].count=7;});
 await page.clock.fastForward(60_000);
 await page.waitForFunction(()=>document.querySelector('#recent-count').textContent==='1');
 assert.deepEqual(await names(),['protect']);
 assert.equal(await rows.locator('.number').textContent(),'7');

 // A batch selection stays stable; leaving the tab and hiding the document stop the minute reads.
 await page.locator('[data-pick="102"]').check();
 const saved=await page.evaluate(()=>{const s=window.__state;return s.recent.pop();});
 await page.clock.fastForward(60_000);
 assert.equal(await page.locator('[data-pick="102"]').isChecked(),true);
 await page.locator('[data-pick="102"]').click();
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
 const page=await openManage(t);const select=page.locator('#common-word-cutoff');
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

test('explicit selection hides each selected entry and reports partial failure accurately',async t=>{
 const page=await openManage(t);await page.locator('[data-pick="101"]').check();await page.locator('[data-pick="102"]').check();assert.equal(await page.locator('#hide-selected').isEnabled(),true);
 // The fixture declines the second write, proving the UI does not turn a partial save into success.
 await page.evaluate(()=>{const original=window.chrome.runtime.sendMessage;window.chrome.runtime.sendMessage=async m=>{if(m.type==='local-preferences'&&m.action==='hide'&&m.entryId==='102')return {ok:false,reason:'synthetic write failure'};return original(m);};});
 await page.click('#hide-selected');await page.waitForFunction(()=>document.querySelector('#feedback').textContent.includes('1 条未保存'));
 assert.match(await page.locator('#feedback').textContent(),/已保存 1 条；1 条未保存：protect：synthetic write failure/);
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
