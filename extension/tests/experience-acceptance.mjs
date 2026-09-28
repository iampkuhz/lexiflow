import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {openActionPopup} from './action-popup.mjs';

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
 const openPopup=async()=>{
  await popup?.close();
  popup=await openActionPopup({context,page,serviceWorker});
  await popup.waitFor(()=>!document.getElementById('enhance-toggle').disabled);
  return popup;
 };
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
  // Open the actual action popup so Chrome supplies its real popup URL and sender identity.
  await openPopup();
  const popupFacts=await popup.evaluate(()=>({width:document.documentElement.clientWidth,height:document.body.scrollHeight,
    logoLoaded:document.querySelector('header img').complete&&document.querySelector('header img').naturalWidth===128,
    logoWidth:document.querySelector('header img').getBoundingClientRect().width,logoHeight:document.querySelector('header img').getBoundingClientRect().height,
    switchWidth:Math.round(document.querySelector('#enhance-toggle').getBoundingClientRect().width),
    status:document.querySelector('#page-status').textContent}));
  assert.equal(popupFacts.width,280);assert.ok(popupFacts.height>=165);assert.equal(popupFacts.logoLoaded,true);assert.equal(popupFacts.logoWidth,24);assert.equal(popupFacts.logoHeight,24);assert.equal(popupFacts.switchWidth,40);
  assert.equal(popupFacts.status,'已开启 · 英文优先');
  await popup.pressSpace('#enhance-toggle');
  await popup.waitFor(()=>document.getElementById('enhance-toggle').disabled===false&&!document.getElementById('enhance-toggle').checked);
  await waitForState(page,'idle');assert.equal(await overlayText(page),'');
  await popup.click('#preferences summary');
  assert.equal(await popup.evaluate(()=>!document.querySelector('#restore-start').hidden),true);
  await popup.click('#restore-start');
  assert.equal(await popup.evaluate(()=>!document.querySelector('#restore-confirmation').hidden),true);
  await popup.click('#restore-cancel');
  assert.equal(await popup.evaluate(()=>!document.querySelector('#restore-confirmation').hidden),false);
  assert.equal(await popup.evaluate(()=>!document.querySelector('#restore-status').hidden),false);
  await page.bringToFront();
  assert.equal(await page.locator('#player').evaluate(e=>e.classList.contains('lexiflow-inline-active')),false);
  const countBefore=JSON.parse(await page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-diagnostics')).counts.requested;
  await setCaption(page,'A reliable disabled caption.',10);
  await page.waitForTimeout(300);
  assert.equal(JSON.parse(await page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-diagnostics')).counts.requested,countBefore);
  await openPopup();await popup.screenshot({path:resolve(root,'popup-disabled.png')});
  await popup.pressSpace('#enhance-toggle');await popup.waitFor(()=>!document.getElementById('enhance-toggle').disabled&&document.getElementById('enhance-toggle').checked);
  await waitForState(page,'ready');assert.equal(await overlayText(page),'A reliable(可靠的) disabled caption.');
  await popup.screenshot({path:resolve(root,'popup-enabled.png')});
  // Turning off while a real delayed request is pending must not allow it to resurrect hints.
  await setCaption(page,'Another reliable caption.',11);
  await page.waitForTimeout(60);
  await page.bringToFront();await openPopup();
  await popup.pressSpace('#enhance-toggle');await popup.waitFor(()=>!document.getElementById('enhance-toggle').disabled&&!document.getElementById('enhance-toggle').checked);
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
  await writeFile(resolve(root,'report.json'),JSON.stringify({status:'PASS',scope:'local authored fixture; real action popup CDP target, native input and screenshot; active tab is synthetic',immediate,layout,diagnostics,checks:['source-multiline','english-before-paint','popup-toggle','disabled-no-requests','pending-cancellation','navigation-reset','stale-toggle-rejected']},null,2));
  await writeFile(resolve(repositoryRoot,'tmp/quality/experience/latest.json'),JSON.stringify({root,status:'PASS'}));
 } finally {
  await popup?.close().catch(()=>undefined);
  await serviceWorker.evaluate(()=>{if(globalThis.__experienceFetch){globalThis.fetch=globalThis.__experienceFetch;delete globalThis.__experienceFetch;}});
 }
 async function overlayState(){return page.locator('#lexiflow-caption-overlay').getAttribute('data-lexiflow-state');}
}
