import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

/** Integration with the real extension in the existing routed local fixture, never a user profile. */
export async function runExperienceAcceptance({page,context,serviceWorker,apiBase,repositoryRoot,setCaption,waitForState,overlayText}) {
 const root=resolve(repositoryRoot,'tmp/quality/experience',String(Date.now()));await mkdir(root,{recursive:true});
 await page.bringToFront();
 const targetId=await serviceWorker.evaluate(async()=> (await chrome.tabs.query({active:true,lastFocusedWindow:true}))[0].id);
 const send=message=>serviceWorker.evaluate(([id,message])=>chrome.tabs.sendMessage(id,message),[targetId,message]);
 // Slow transport guarantees the pre-paint test cannot accidentally observe a completed response.
 await serviceWorker.evaluate(base=>{
  globalThis.__experienceFetch=globalThis.fetch;
  const previous=globalThis.fetch;
  globalThis.fetch=async(...args)=>{await new Promise(r=>setTimeout(r,220));return previous(...args);};
 },apiBase);
 let popup;
 try {
  const immediate=await page.evaluate(async()=>{
   const container=document.querySelector('#ytp-caption-window-container');
   container.replaceChildren();
   for(const text of ['A reliable caption','remains on separate rows.']){
    const row=document.createElement('span');row.className='caption-visual-line';row.style.display='block';
    row.append(Object.assign(document.createElement('span'),{className:'ytp-caption-segment',textContent:text}));container.append(row);
   }
   await new Promise(requestAnimationFrame);
   const host=document.querySelector('#lexiflow-caption-overlay');const line=host.shadowRoot.querySelector('.line');
   return {text:line.textContent,breaks:line.querySelectorAll('br').length,masked:document.querySelector('#player').classList.contains('lexiflow-inline-active')};
  });
  assert.deepEqual(immediate,{text:'A reliable caption remains on separate rows.',breaks:1,masked:true});
  await waitForState(page,'ready');
  assert.equal(await overlayText(page),'A reliable(可靠的) caption remains on separate rows.');
  const layout=await page.evaluate(()=>{
   const line=document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.line');
   const box=line.getBoundingClientRect();const player=document.querySelector('#player').getBoundingClientRect();
   return {breaks:line.querySelectorAll('br').length,height:box.height,font:parseFloat(getComputedStyle(line).fontSize),inside:box.left>=player.left&&box.right<=player.right};
  });
  assert.equal(layout.breaks,1);assert.ok(layout.height>layout.font*1.5);assert.equal(layout.inside,true);
  await page.locator('#player').screenshot({path:resolve(root,'source-multiline.png')});
  const before=await send({type:'page-enhancement',action:'read'});assert.equal(before.ok,true);assert.equal(before.enabled,true);
  // Popup document is real. Only active-tab selection is bound to the fixture rather than this test tab.
  popup=await context.newPage();
  await popup.setViewportSize({width:280,height:110});
  await popup.addInitScript(id=>{chrome.tabs.query=async()=>[{id}];},targetId);
  await popup.goto(`chrome-extension://${new URL(serviceWorker.url()).host}/popup.html`);
  const toggle=popup.getByLabel('当前页面启用字幕增强');await toggle.waitFor();
  await popup.waitForFunction(()=>!document.getElementById('enhance-toggle').disabled);
  await toggle.uncheck();await popup.waitForFunction(()=>!document.getElementById('enhance-toggle').disabled);
  await waitForState(page,'idle');assert.equal(await overlayText(page),'');
  assert.equal(await page.locator('#player').evaluate(e=>e.classList.contains('lexiflow-inline-active')),false);
  const countBefore=JSON.parse(await page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-diagnostics')).counts.requested;
  await setCaption(page,'A reliable disabled caption.',10);
  await page.waitForTimeout(300);
  assert.equal(JSON.parse(await page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-diagnostics')).counts.requested,countBefore);
  await popup.screenshot({path:resolve(root,'popup-disabled.png')});
  await toggle.check();await popup.waitForFunction(()=>!document.getElementById('enhance-toggle').disabled);
  await waitForState(page,'ready');assert.equal(await overlayText(page),'A reliable(可靠的) disabled caption.');
  await popup.screenshot({path:resolve(root,'popup-enabled.png')});
  // Turning off while a real delayed request is pending must not allow it to resurrect hints.
  await setCaption(page,'Another reliable caption.',11);
  await page.waitForTimeout(60);
  await toggle.uncheck();await popup.waitForFunction(()=>!document.getElementById('enhance-toggle').disabled);
  await page.waitForTimeout(400);assert.equal(await overlayState(),'idle');assert.equal(await overlayText(page),'');
  // Navigation resets only this page's in-memory switch; stale popup cannot target a new page.
  await page.evaluate(()=>{document.dispatchEvent(new Event('yt-navigate-start'));history.pushState({},'', '/watch?v=local-next');document.dispatchEvent(new Event('yt-navigate-finish'));});
  const after=await send({type:'page-enhancement',action:'read'});assert.equal(after.enabled,true);assert.notEqual(after.pageKey,before.pageKey);
  assert.deepEqual(await send({type:'page-enhancement',action:'set',enabled:false,pageKey:before.pageKey}),{ok:false});
  await waitForState(page,'ready');
  await page.evaluate(()=>{history.replaceState({},'', '/watch?v=lexiflow-e2e');window.dispatchEvent(new PopStateEvent('popstate'));});
  await waitForState(page,'ready');
  const diagnostics=JSON.parse(await page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-diagnostics'));
  assert.ok(diagnostics.counts['cancelled-in-flight']>=1);
  await writeFile(resolve(root,'report.json'),JSON.stringify({status:'PASS',scope:'local authored fixture; real extension popup/message/render; active-tab query fixture-bound',immediate,layout,diagnostics,checks:['source-multiline','english-before-paint','popup-toggle','disabled-no-requests','pending-cancellation','navigation-reset','stale-toggle-rejected']},null,2));
  await writeFile(resolve(repositoryRoot,'tmp/quality/experience/latest.json'),JSON.stringify({root,status:'PASS'}));
 } finally {
  await popup?.close();
  await serviceWorker.evaluate(()=>{if(globalThis.__experienceFetch){globalThis.fetch=globalThis.__experienceFetch;delete globalThis.__experienceFetch;}});
 }
 async function overlayState(){return page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-state');}
}
