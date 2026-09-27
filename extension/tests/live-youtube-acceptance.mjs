import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
/** 显式启用的真实来源核验：隔离浏览器、不保存原文或截图，不操作用户标签页。 */
export async function runLiveYoutubeAcceptance({context,serviceWorker,repositoryRoot,url}) {
 const page=await context.newPage();
 const sourceResponses=[];
 page.on('response',async response=>{
  if(new URL(response.url()).pathname!=='/api/timedtext')return;
  let size=null,eventCount=null;
  try{const text=await response.text();size=text.length;try{eventCount=JSON.parse(text).events?.length??null;}catch{}}catch{}
  sourceResponses.push({status:response.status(),size,eventCount});
 });
 await serviceWorker.evaluate(()=>{
  globalThis.__liveOriginalFetch=globalThis.fetch;
  globalThis.__liveSummary={requests:0,withSourceTiming:0,appended:0,retained:0};
  globalThis.fetch=(...args)=>{
   const request=JSON.parse(args[1].body),segments=request.currentSnapshot.captions.flatMap(group=>group.segments);
   const s=globalThis.__liveSummary;s.requests++;s.withSourceTiming+=request.currentSnapshot.captions.some(group=>group.startMs!==null)?1:0;
   s.appended+=segments.filter(segment=>segment.append).length;s.retained+=segments.filter(segment=>!segment.append).length;
   return globalThis.__liveOriginalFetch(...args);
  };
 });
 try {
  await page.goto(url,{waitUntil:'domcontentloaded',timeout:60000});
  await page.locator('video').waitFor({timeout:30000});
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2,undefined,{timeout:30000});
  const button=page.locator('.ytp-subtitles-button');
  if(await button.count() && await button.getAttribute('aria-pressed')!=='true') await button.click();
  await page.locator('video').evaluate(async video=>{video.muted=true;video.currentTime=130;await video.play();});
  await page.waitForFunction(()=>document.querySelectorAll('.ytp-caption-segment').length>0,undefined,{timeout:45000});
  const samples=[];
  for(let i=0;i<30;i++){
   samples.push(await page.evaluate(()=>{
    const host=document.querySelector('#lexiflow-caption-overlay'),line=host?.shadowRoot?.querySelector('.line');
    return {sourceSegments:document.querySelectorAll('.ytp-caption-segment').length,enhanced:!!line?.textContent,state:host?.dataset.lexiflowState,masked:document.querySelector('.html5-video-player')?.classList.contains('lexiflow-inline-active')};
   }));
   await page.waitForTimeout(500);
  }
  const summary=await serviceWorker.evaluate(()=>globalThis.__liveSummary);
  assert.ok(summary.requests>1,'real playback must actually produce requests');
  assert.ok(summary.retained>0,'real source must exercise retained incremental segments');
  assert.ok(samples.filter(s=>s.sourceSegments>0&&s.enhanced&&s.masked).length>=15,'enhanced English remains visible during actual captions');
  const report={status:'PASS',scope:'isolated Chromium real YouTube playback + built-in local API; no real captions or URLs retained',summary,samples,
   limitations:['Real source metadata may be unavailable on non-JSON3 playback paths; unknown values remain null.','Hint-node stability is asserted by the deterministic synthetic browser suite, not inferred from this sampling.']};
  await mkdir(resolve(repositoryRoot,'tmp/quality/incremental-live'),{recursive:true});
  await writeFile(resolve(repositoryRoot,'tmp/quality/incremental-live/latest.json'),JSON.stringify(report,null,2));
 } catch(error) {
  const failure=await page.evaluate(()=>({video:!!document.querySelector('video'),currentTime:document.querySelector('video')?.currentTime,readyState:document.querySelector('video')?.readyState,
    error:document.querySelector('video')?.error?.code,captionButton:document.querySelector('.ytp-subtitles-button')?.getAttribute('aria-label'),
    captionEnabled:document.querySelector('.ytp-subtitles-button')?.getAttribute('aria-pressed'),sourceSegments:document.querySelectorAll('.ytp-caption-segment').length,
    requiresVerification:/confirm you.re not a bot|确认您不是机器人|sign in to confirm/i.test(document.body.innerText),unavailable:!!document.querySelector('.ytp-error') }));
  await mkdir(resolve(repositoryRoot,'tmp/quality/incremental-live'),{recursive:true});
  await writeFile(resolve(repositoryRoot,'tmp/quality/incremental-live/failure.json'),JSON.stringify({...failure,sourceResponses},null,2));
  throw error;
 } finally {await page.close();await serviceWorker.evaluate(()=>{globalThis.fetch=globalThis.__liveOriginalFetch;delete globalThis.__liveOriginalFetch;delete globalThis.__liveSummary;});}
}
