import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

/** 从首次采集即为三行的原生 DOM 出发；延迟真实 API 而不是伪造翻译结果。 */
export async function runMultilineAcceptance({page,serviceWorker,repositoryRoot,setCaption,waitForState}) {
  const root=resolve(repositoryRoot,'tmp/quality/multiline',String(Date.now()));
  await mkdir(root,{recursive:true});
  const cases=[];
  await setCaption(page,'',50);await waitForState(page,'idle');
  await serviceWorker.evaluate(()=>{
    globalThis.__multilineOriginalFetch=globalThis.fetch;
    globalThis.__multilineRequests=[];globalThis.__multilineWaiters=[];globalThis.__multilineHold=true;globalThis.__multilineSettled=0;
    globalThis.fetch=async(...args)=>{
      globalThis.__multilineRequests.push(JSON.parse(args[1].body));
      try {
        if(globalThis.__multilineHold)await new Promise(resolve=>globalThis.__multilineWaiters.push(resolve));
        return await globalThis.__multilineOriginalFetch(...args);
      } finally {globalThis.__multilineSettled++;}
    };
  });
  const release=()=>serviceWorker.evaluate(()=>{globalThis.__multilineHold=false;globalThis.__multilineWaiters.splice(0).forEach(resolve=>resolve());});
  const hold=()=>serviceWorker.evaluate(()=>{globalThis.__multilineHold=true;});
  const requests=()=>serviceWorker.evaluate(()=>globalThis.__multilineRequests);
  const waitForHeld=async()=>{
    for(let i=0;i<100;i++){
      if(await serviceWorker.evaluate(()=>globalThis.__multilineWaiters.length>0))return;
      await page.waitForTimeout(20);
    }
    assert.fail('multiline fixture must really hold an in-flight request');
  };
  const capture=()=>page.evaluate(()=>{
    const line=document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.line');
    return {
      english:[...line.children].filter(node=>!node.classList.contains('gloss')).map(node=>node.textContent).join(''),
      breaks:line.querySelectorAll('br').length,glosses:line.querySelectorAll('.gloss').length,
      masked:document.querySelector('#player').classList.contains('lexiflow-inline-active')
    };
  });
  const sourceRows=async rows=>{
    await page.evaluate(async rows=>{
      const container=document.querySelector('#ytp-caption-window-container');container.replaceChildren();
      for(const text of rows){const row=document.createElement('span');row.className='caption-visual-line';row.style.display='block';row.append(Object.assign(document.createElement('span'),{className:'ytp-caption-segment',textContent:text}));container.append(row);}
      await new Promise(requestAnimationFrame);
    },rows);
    return capture();
  };
  try {
    const initial=['An earlier opening','A reliable method','A continuing ending'];
    const first=await sourceRows(initial);
    assert.deepEqual(first,{english:initial.join(' '),breaks:2,glosses:0,masked:true});
    await waitForHeld();
    const request=(await requests())[0];
    const segments=request.currentSnapshot.captions.flatMap(group=>group.segments);
    assert.equal(segments.map(segment=>segment.text).join(''),initial.join(' '));
    assert.deepEqual([...new Set(segments.map(segment=>segment.line))],[0,1,2]);
    assert.ok(segments.every(segment=>segment.append));
    cases.push({id:'native-three-lines-first-capture',sourceLines:3,requestLineNumbers:[0,1,2],...first});
    await page.locator('#player').screenshot({path:resolve(root,'three-lines-before-api.png')});

    const rolled=['A reliable method','A continuing ending','A newly appended tail'];
    const second=await sourceRows(rolled);
    assert.deepEqual(second,{english:rolled.join(' '),breaks:2,glosses:0,masked:true});
    assert.equal((await requests()).length,1,'latest observation must wait behind the actual in-flight request');
    await release();await waitForState(page,'ready');
    assert.equal((await capture()).english,rolled.join(' '));
    assert.equal((await capture()).glosses,1);
    const incremental=(await requests()).at(-1).currentSnapshot.captions.flatMap(group=>group.segments);
    assert.equal(incremental.filter(segment=>segment.append).map(segment=>segment.text).join(''),' A newly appended tail');
    assert.equal((await requests()).length,2);
    cases.push({id:'three-line-roll-during-slow-api',sourceLines:3,requests:2,...await capture()});
    await page.locator('#player').screenshot({path:resolve(root,'three-lines-after-api.png')});

    await page.evaluate(()=>{window.__multilineGloss=document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss');});
    await sourceRows(['A reliable method A continuing ending','A newly appended tail']);
    const reflow=await capture();assert.equal(reflow.breaks,1);assert.equal(reflow.english,rolled.join(' '));
    await sourceRows(rolled);await page.waitForTimeout(80);
    assert.equal((await requests()).length,2,'pure line changes must not call API');
    assert.equal(await page.evaluate(()=>document.querySelector('#lexiflow-caption-overlay').shadowRoot.querySelector('.gloss')===window.__multilineGloss),true);
    cases.push({id:'three-two-three-source-reflow',requests:2,retainedGlossNode:true,...await capture()});

    await hold();
    const corrected=['A reliable method','A corrected middle line','A newly appended tail'];
    const correction=await sourceRows(corrected);assert.equal(correction.english,corrected.join(' '));assert.equal(correction.breaks,2);
    await waitForHeld();await release();await waitForState(page,'ready');
    assert.equal((await capture()).english,corrected.join(' '));assert.equal((await capture()).glosses,1);
    cases.push({id:'middle-source-line-correction',sourceLines:3,...await capture()});

    await hold();await sourceRows(['A different reliable opening','Another middle row','The final row']);await waitForHeld();
    await page.evaluate(async()=>{document.querySelector('#ytp-caption-window-container').style.display='none';await new Promise(requestAnimationFrame);});
    assert.deepEqual(await capture(),{english:'',breaks:0,glosses:0,masked:false});
    await release();
    for(let i=0;i<100;i++){
      if(await serviceWorker.evaluate(()=>globalThis.__multilineSettled===globalThis.__multilineRequests.length))break;
      await page.waitForTimeout(20);
    }
    assert.equal(await serviceWorker.evaluate(()=>globalThis.__multilineSettled===globalThis.__multilineRequests.length),true);
    await page.waitForTimeout(80);
    assert.deepEqual(await capture(),{english:'',breaks:0,glosses:0,masked:false});
    cases.push({id:'hidden-three-lines-reject-late-response',...await capture()});
    await writeFile(resolve(root,'report.json'),JSON.stringify({status:'PASS',inputMode:'native-source-lines-with-real-local-api',cases},null,2));
  } finally {
    await release();
    await serviceWorker.evaluate(()=>{globalThis.fetch=globalThis.__multilineOriginalFetch;delete globalThis.__multilineOriginalFetch;});
    await page.evaluate(()=>{document.querySelector('#ytp-caption-window-container').style.display='';});
    await setCaption(page,'',55);await waitForState(page,'idle');
  }
}
