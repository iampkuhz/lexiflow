import {writeFile} from 'node:fs/promises';

/** 返回可实际接收鼠标事件的点；弹窗扩容或折叠内容尚未可见时不点击。 */
export function popupClickPoint(selector) {
  const element=document.querySelector(selector);
  if(!element||element.disabled) return null;
  element.scrollIntoView({block:'center',inline:'nearest'});
  const rect=element.getBoundingClientRect();
  if(rect.width<=0||rect.height<=0) return null;
  const x=rect.left+rect.width/2,y=rect.top+rect.height/2;
  const target=document.elementFromPoint(x,y);
  if(!target||(target!==element&&!element.contains(target))) return null;
  return {x,y};
}

/** CDP attaches to Chrome's real action popup, which Playwright does not list as a Page. */
export async function openActionPopup({context,page,serviceWorker}) {
  await page.bringToFront();
  const browserSession=await context.newCDPSession(page);
  const url=`chrome-extension://${new URL(serviceWorker.url()).host}/popup.html`;
  let target;
  try {
    const previous=new Set((await browserSession.send('Target.getTargets')).targetInfos.map(info=>info.targetId));
    await serviceWorker.evaluate(()=>chrome.action.openPopup());
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
      const end=Date.now()+10000;
      let previous=null;
      while(Date.now()<end) {
        const point=await evaluate(popupClickPoint,selector);
        // 连续两次可见且位置稳定，防止 details 展开与原生 popup 自动扩容间的竞争。
        if(point&&previous&&point.x===previous.x&&point.y===previous.y) {
          await command('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
          await command('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
          return;
        }
        previous=point;
        await new Promise(resolve=>setTimeout(resolve,50));
      }
      throw new Error(`popup element not actionable: ${selector}`);
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
