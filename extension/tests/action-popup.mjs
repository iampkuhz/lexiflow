import {writeFile} from 'node:fs/promises';

/** Fail closed unless this isolated context owns exactly one normal Chrome window. */
export async function openInUniqueNormalWindow({windows,openPopup}) {
  if (!Array.isArray(windows)) throw new Error('Chrome normal windows unavailable');
  const normal=windows.filter(window=>window?.type==='normal');
  if (normal.length!==1 || !Number.isInteger(normal[0]?.id)) {
    throw new Error(`Expected exactly one normal Chrome window; found ${normal.length}`);
  }
  return openPopup(normal[0].id);
}

/** Focus the owned window before invoking Chrome's real action popup API. */
export async function openChromeActionPopup({windows,serviceWorker}) {
  return openInUniqueNormalWindow({windows,openPopup:windowId=>
    serviceWorker.evaluate(async id=>{
      await chrome.windows.update(id,{focused:true});
      return chrome.action.openPopup({windowId:id});
    },windowId)});
}

/** Fixed popup-only geometry check, serialized into the real popup Runtime. */
export function measurePopupClickTarget(selector) {
  const element=document.querySelector(selector);
  if(!element) throw new Error(`missing popup element ${selector}`);
  const rect=element.getBoundingClientRect();
  const style=getComputedStyle(element);
  const x=rect.left+rect.width/2,y=rect.top+rect.height/2;
  const hit=document.elementFromPoint(x,y);
  const visible=element.getClientRects().length>0&&style.display!=='none'&&style.visibility!=='hidden'&&style.visibility!=='collapse'&&style.opacity!=='0';
  const enabled=!element.disabled&&element.getAttribute('aria-disabled')!=='true';
  const inViewport=rect.width>0&&rect.height>0&&rect.left>=0&&rect.top>=0&&rect.right<=innerWidth&&rect.bottom<=innerHeight;
  const hitTarget=!!hit&&(hit===element||element.contains(hit));
  if(!visible||!enabled||!inViewport||!hitTarget) return null;
  return {x,y,left:rect.left,top:rect.top,width:rect.width,height:rect.height,viewportWidth:innerWidth,viewportHeight:innerHeight,scrollX,scrollY};
}

/** Popup-only bounded wait; requires three consecutive identical actionable measurements. */
export async function waitForPopupClickTarget(selector,evaluate) {
  const end=Date.now()+10000;
  let previous=null,stable=0;
  while(Date.now()<end) {
    const point=await evaluate(measurePopupClickTarget,selector);
    if(point&&previous&&Object.keys(point).every(key=>point[key]===previous[key])) stable++;
    else stable=point?1:0;
    if(stable>=3) return {x:point.x,y:point.y};
    previous=point;
    await new Promise(resolve=>setTimeout(resolve,50));
  }
  throw new Error(`popup click target not actionable and stable: ${selector}`);
}

/** CDP attaches to Chrome's real action popup, which Playwright does not list as a Page. */
export async function openActionPopup({context,page,serviceWorker}) {
  await page.bringToFront();
  const browserSession=await context.newCDPSession(page);
  const url=`chrome-extension://${new URL(serviceWorker.url()).host}/popup.html`;
  let target;
  try {
    const previous=new Set((await browserSession.send('Target.getTargets')).targetInfos.map(info=>info.targetId));
    const windows=await serviceWorker.evaluate(()=>chrome.windows.getAll({windowTypes:['normal']}));
    await openChromeActionPopup({windows,serviceWorker});
    for(let attempt=0;attempt<40;attempt++) {
      target=(await browserSession.send('Target.getTargets')).targetInfos.find(info=>info.url===url&&info.type==='page'&&!previous.has(info.targetId));
      if(target) break;
      await new Promise(resolve=>setTimeout(resolve,50));
    }
    if(!target) throw new Error(`Chrome action popup target unavailable: ${url}`);
    const {sessionId}=await browserSession.send('Target.attachToTarget',{targetId:target.targetId,flatten:false});
    let nextId=0;
    const command=(method,params={})=>new Promise((resolve,reject)=>{
      const id=++nextId;
      const timeout=setTimeout(()=>{browserSession.off('Target.receivedMessageFromTarget',onMessage);reject(new Error(`popup CDP ${method} timed out`));},10000);
      function onMessage(event) {
        if(event.sessionId!==sessionId) return;
        const message=JSON.parse(event.message);
        if(message.id!==id) return;
        clearTimeout(timeout);browserSession.off('Target.receivedMessageFromTarget',onMessage);
        if(message.error) reject(new Error(`popup CDP ${method}: ${message.error.message}`));
        else resolve(message.result);
      }
      browserSession.on('Target.receivedMessageFromTarget',onMessage);
      browserSession.send('Target.sendMessageToTarget',{sessionId,message:JSON.stringify({id,method,params})})
        .catch(error=>{clearTimeout(timeout);browserSession.off('Target.receivedMessageFromTarget',onMessage);reject(error);});
    });
    const evaluate=async(fn,arg)=>{
      const expression=`(${fn.toString()})(${JSON.stringify(arg)})`;
      const result=await command('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
      if(result.exceptionDetails) throw new Error(result.exceptionDetails.text);
      return result.result.value;
    };
    const waitFor=async(fn,arg)=>{
      const end=Date.now()+10000;
      while(Date.now()<end) {
        if(await evaluate(fn,arg)) return;
        await new Promise(resolve=>setTimeout(resolve,50));
      }
      throw new Error('popup condition timed out');
    };
    const click=async(selector)=>{
      const point=await waitForPopupClickTarget(selector,evaluate);
      await command('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1});
      await command('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',clickCount:1});
    };
    const pressSpace=async(selector)=>{
      await evaluate(selector=>document.querySelector(selector).focus(),selector);
      const key={key:' ',code:'Space',windowsVirtualKeyCode:32,nativeVirtualKeyCode:32};
      await command('Input.dispatchKeyEvent',{type:'keyDown',...key});
      await command('Input.dispatchKeyEvent',{type:'keyUp',...key});
    };
    const screenshot=async({path})=>{
      const result=await command('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
      await writeFile(path,Buffer.from(result.data,'base64'));
    };
    const close=async()=>{
      await browserSession.send('Target.closeTarget',{targetId:target.targetId}).catch(()=>undefined);
      await browserSession.detach().catch(()=>undefined);
    };
    await waitFor(()=>document.readyState==='complete');
    return {evaluate,waitFor,click,pressSpace,screenshot,close};
  } catch(error) {
    await browserSession.detach().catch(()=>undefined);
    throw error;
  }
}
