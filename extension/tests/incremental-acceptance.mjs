import assert from 'node:assert/strict';
/** 使用真实扩展和 API；只延迟网络以确定性暴露刷新与丢片段问题。 */
export async function runIncrementalAcceptance({page,serviceWorker,setCaption,waitForState,overlayText}) {
 await serviceWorker.evaluate(()=>{
  globalThis.__incrementalOriginalFetch=globalThis.fetch;globalThis.__incrementalRequests=[];
  globalThis.fetch=async(...args)=>{const request=JSON.parse(args[1].body);globalThis.__incrementalRequests.push(request);await new Promise(r=>setTimeout(r,250));return globalThis.__incrementalOriginalFetch(...args);};
 });
 try {
  await setCaption(page,'A reliable',40);await waitForState(page,'ready');
  await page.evaluate(()=>{window.__retainedGloss=document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss');});
  const before=await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.length);
  await setCaption(page,'A reliable method',40.2);
  const immediate=await page.evaluate(async()=>{await new Promise(requestAnimationFrame);const line=document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.line');return{text:line.textContent,same:line.querySelector('.gloss')===window.__retainedGloss};});
  assert.deepEqual(immediate,{text:'A reliable(可靠的) method',same:true});
  await waitForState(page,'ready');
  const request=await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.at(-1));
  assert.equal(request.currentSnapshot.captions.flatMap(g=>g.segments).filter(s=>s.append).map(s=>s.text).join(''),' method');
  assert.equal(request.lastRequestedSnapshot.captions.flatMap(g=>g.segments).map(s=>s.text).join(''),'A reliable');
  assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__retainedGloss),true);
  await setCaption(page,'reliable method',40.4);await page.waitForTimeout(300);
  assert.equal(await overlayText(page),'reliable(可靠的) method');
  assert.equal(await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.length),before+1);
  assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__retainedGloss),true);
  // 原生 DOM 整体重建且改分行，不新增请求、不替换中文节点。
  await page.evaluate(()=>{
    const container=document.querySelector('#ytp-caption-window-container');container.replaceChildren();
    for(const text of ['reliable','method']){const row=document.createElement('span');row.className='caption-visual-line';row.style.display='block';row.append(Object.assign(document.createElement('span'),{className:'ytp-caption-segment',textContent:text}));container.append(row);}
  });
  await page.waitForTimeout(300);assert.equal(await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.length),before+1);
  assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__retainedGloss),true);
  assert.equal(await page.locator('#lexiflow-caption-overlay br').count(),1);
  // 同一词条在新的增量响应再次命中，也只能保留一处提示且不能替换旧节点。
  await setCaption(page,'reliable method reliable',40.8);await page.waitForTimeout(350);await waitForState(page,'ready');
  assert.equal(await page.locator('#lexiflow-caption-overlay .gloss').count(),1);
  assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__retainedGloss),true);
  // 慢请求返回时字幕已追加，仍可见的旧词必须获得提示。
  await setCaption(page,'Another reliable',41);await page.waitForTimeout(70);await setCaption(page,'Another reliable result',41.1);
  await waitForState(page,'ready');assert.equal(await overlayText(page),'Another reliable(可靠的) result');
  // 原生 roll-up 仅由 CSS transform 改变可见行：没有 DOM mutation/timeupdate 也要即时采集。
  await page.evaluate(()=>{
    const container=document.querySelector('#ytp-caption-window-container');container.replaceChildren();
    const window=document.createElement('div');window.className='caption-window';window.style.cssText='height:48px;overflow:hidden';
    const content=document.createElement('span');content.className='captions-text';content.style.cssText='display:block;transition:transform 400ms linear;transform:translateY(0)';
    for(const text of ['An older line','A reliable method','new words']){const row=document.createElement('span');row.className='caption-visual-line';row.style.cssText='display:block;height:24px;line-height:24px';row.append(Object.assign(document.createElement('span'),{className:'ytp-caption-segment',textContent:text}));content.append(row);}
    window.append(content);container.append(window);
  });
  await waitForState(page,'ready');
  const motionBefore=await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.length);
  await page.evaluate(()=>{window.__motionGloss=document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss');document.querySelector('.captions-text').style.transform='translateY(-24px)';});
  await page.waitForFunction(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.line').textContent.includes('new words'),undefined,{timeout:180});
  assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__motionGloss),true);
  await page.waitForTimeout(500);
  assert.equal(await overlayText(page),'A reliable(可靠的) method new words');
  assert.equal(await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.length),motionBefore+1);
  // 原生动画复位与前缀节点移除不在同一帧：不得回显旧行或丢掉刚出现的新行。
  const stableBottom=await page.locator('#lexiflow-caption-overlay').evaluate(host=>host.style.bottom);
  await page.evaluate(()=>{
    window.__rollupFrames=[];window.__recordRollup=true;
    const collect=()=>{if(!window.__recordRollup)return;const host=document.querySelector('#lexiflow-caption-overlay');window.__rollupFrames.push({text:host.shadowRoot.querySelector('.line').textContent,bottom:host.style.bottom});requestAnimationFrame(collect);};requestAnimationFrame(collect);
    const content=document.querySelector('.captions-text');content.style.transition='none';content.style.transform='translateY(0)';
  });
  await page.waitForTimeout(80);
  assert.equal(await overlayText(page),'A reliable(可靠的) method new words');
  await page.evaluate(()=>{document.querySelector('.captions-text').firstElementChild.remove();});
  await page.waitForTimeout(80);
  const frames=await page.evaluate(()=>{window.__recordRollup=false;return window.__rollupFrames;});
  assert.ok(frames.length>=2);
  assert.ok(frames.every(frame=>frame.text==='A reliable(可靠的) method new words'),JSON.stringify(frames));
  assert.ok(frames.every(frame=>frame.bottom===stableBottom),JSON.stringify(frames));
  assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__motionGloss),true);
  assert.equal(await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.length),motionBefore+1);
  // seek 建立新来源边界，同一节点/相同文字可以合法重新出现。
  await page.evaluate(()=>{const video=document.querySelector('video');video.dispatchEvent(new Event('seeking',{bubbles:true}));document.querySelector('.captions-text').firstElementChild.querySelector('.ytp-caption-segment').textContent='An older line';video.dispatchEvent(new Event('seeked',{bubbles:true}));});
  await page.waitForFunction(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.line').textContent.includes('An older line'));
  // 页面桥为不可信输入：已匹配轨道变为歧义时，不能沿用旧轨道标签。
  await setCaption(page,'A reliable native',42);await waitForState(page,'ready');
  await page.evaluate(()=>window.addEventListener('message',event=>{if(event.data?.type==='lexiflow-native-captions')window.__sourceMessageEvidence={origin:event.origin,sameWindow:event.source===window,videoId:new URL(location.href).searchParams.get('v'),time:document.querySelector('video').currentTime};}));
  const emit=trackKey=>page.evaluate(trackKey=>window.postMessage({type:'lexiflow-native-captions',track:{videoId:'lexiflow-e2e',trackKey,fragments:[
    {text:'A reliable native',startMs:0,endMs:50000,offsetMs:0,windowId:'1',append:false}]}},location.origin),trackKey);
  await emit('en:source');await page.waitForTimeout(350);await waitForState(page,'ready');
  const actualTrack=await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.at(-1).trackKey);
  assert.equal(actualTrack,'en:source',JSON.stringify(await page.evaluate(()=>({event:window.__sourceMessageEvidence,diagnostics:JSON.parse(document.querySelector('#lexiflow-caption-overlay').dataset.lexiflowDiagnostics)}))));
  assert.equal(await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.at(-1).currentSnapshot.captions[0].startMs),0);
  await emit('en:ambiguous');await page.waitForTimeout(350);await waitForState(page,'ready');
  assert.equal(await serviceWorker.evaluate(()=>globalThis.__incrementalRequests.at(-1).trackKey),null);
 } finally { await serviceWorker.evaluate(()=>{globalThis.fetch=globalThis.__incrementalOriginalFetch;delete globalThis.__incrementalOriginalFetch;delete globalThis.__incrementalRequests;}); }
}
