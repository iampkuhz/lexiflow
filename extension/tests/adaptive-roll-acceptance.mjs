import assert from "node:assert/strict";

export async function runAdaptiveRollAcceptance({page,serviceWorker,waitForState}) {
  await serviceWorker.evaluate(()=>chrome.storage.local.remove("captionLineLimit"));
  try {
    await page.evaluate(()=>{
      const player=document.getElementById("player"), video=player.querySelector("video");
      const captions=document.getElementById("ytp-caption-window-container");
      window.__adaptiveCue=(texts,{reset=false,windows=false,width}={})=>{
        if(reset)video.dispatchEvent(new Event("seeking",{bubbles:true}));
        if(width)player.style.width=width+"px";
        const nodes=[];
        let group;
        for(const text of texts){
          if(!group||windows){
            group=document.createElement("div"); group.className="caption-window";
            group.style.cssText="line-height:37px;overflow:hidden";
            nodes.push(group);
          }
          const row=document.createElement("div"); row.className="caption-visual-line";
          const segment=document.createElement("span"); segment.className="ytp-caption-segment";
          segment.textContent=text;row.append(segment);group.append(row);
        }
        captions.replaceChildren(...nodes);
        if(reset)video.dispatchEvent(new Event("seeked",{bubbles:true}));
      };
    });
    const waitActive = (count) => page.waitForFunction(want => {
      const root=document.querySelector("#lexiflow-caption-overlay")?.shadowRoot;
      return root?.querySelectorAll(".line > .caption-row:not([data-history])").length===want;
    },count);
    for (const rate of [1,2]) {
      await page.evaluate(rate=>{
        document.querySelector("video").playbackRate=rate;
        window.__adaptiveCue(["FIRST we can all do the work","SECOND we can do this in time"],{reset:true,width:462});
      },rate);
      await waitForState(page,"no-pending");
      await waitActive(2);
      const trace=await page.evaluate(async rate=>{
        const root=document.querySelector("#lexiflow-caption-overlay").shadowRoot;
        const viewport=root.querySelector(".viewport");
        const rows=()=>[...root.querySelectorAll(".line > .caption-row")];
        const older=rows()[1], startTop=older.getBoundingClientRect().top;
        if(rate===2){
          // A batch may briefly remove its old nodes before mounting the next window.
          window.__adaptiveCue([]);
          await new Promise(resolve=>setTimeout(resolve,90));
        }
        const frames=[], start=performance.now();
        window.__adaptiveCue(["THIRD we can go back this way","FOURTH it will be all the same"],{windows:rate===2});
        while(performance.now()-start<520){
          await new Promise(requestAnimationFrame);
          const clip=viewport.getBoundingClientRect();
          const visible=row=>{
            const box=row?.getBoundingClientRect();
            return box? Math.max(0,Math.min(box.bottom,clip.bottom)-Math.max(box.top,clip.top)):0;
          };
          const current=rows(), next=current.find(row=>row.textContent.startsWith("THIRD")),
            last=current.find(row=>row.textContent.startsWith("FOURTH"));
          frames.push({ms:performance.now()-start,old:visible(older),next:visible(next),last:visible(last)});
        }
        return {held:rows()[0]===older&&older.hasAttribute("data-history"), count:rows().length,
          oldMoved:startTop-older.getBoundingClientRect().top,
          handoff:frames.some(f=>f.old>10&&f.next>10&&f.last<10),
          lastIn:frames.find(f=>f.last>=25)?.ms};
      },rate);
      assert.ok(trace.held&&trace.count===3&&trace.oldMoved>25&&trace.handoff,JSON.stringify({rate,...trace}));
      assert.ok(trace.lastIn<(rate===2?230:370),JSON.stringify({rate,...trace}));
    }
    // Merge brief same-window lines, then adapt to a narrow player without creating a historical duplicate.
    await page.evaluate(()=>{
      window.__adaptiveCue(["We can see it all.","We can do it too."],{reset:true,width:900});
    });
    await waitActive(1);
    await page.evaluate(()=>{
      document.getElementById("player").style.width="230px";
      window.dispatchEvent(new Event("resize"));
    });
    await waitActive(2);
    assert.equal(await page.locator("#lexiflow-caption-overlay .line .caption-row[data-history]").count(),0);
    await page.evaluate(()=>{
      document.getElementById("player").style.width="900px";
      window.dispatchEvent(new Event("resize"));
    });
    await waitActive(1);
    const text=await page.locator("#lexiflow-caption-overlay .line .caption-row").textContent();
    assert.equal(text,"We can see it all. We can do it too.");
    await page.evaluate(()=>window.__adaptiveCue(["We can see it.","It is right there."],{reset:true,width:900,windows:true}));
    await waitActive(2);
    await page.evaluate(()=>window.__adaptiveCue([]));
    await page.waitForFunction(()=>!document.querySelector("#lexiflow-caption-overlay")?.shadowRoot.querySelector(".line > .caption-row"));
  } finally {
    await serviceWorker.evaluate(()=>chrome.storage.local.set({captionLineLimit:2}));
    await page.evaluate(()=>{
      document.getElementById("player").style.width="";
      document.querySelector("video").playbackRate=1;
      delete window.__adaptiveCue;
    });
  }
}
