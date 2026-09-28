import {writeFile} from 'node:fs/promises';

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
      const point=await evaluate(selector=>{
        const element=document.querySelector(selector);
        if(!element) throw new Error(`missing popup element ${selector}`);
        const rect=element.getBoundingClientRect();return {x:rect.left+rect.width/2,y:rect.top+rect.height/2};
      },selector);
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
