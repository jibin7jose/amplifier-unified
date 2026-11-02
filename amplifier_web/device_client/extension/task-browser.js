// Fixed executor over Chrome's debugger transport. No remote script/CDP endpoint.
let attachedTab=null, taskWorld=null, attachmentVersion=0;
const taskSnapshots=new Map();
const taskCommand=(tabId,method,params={})=>chrome.debugger.sendCommand({tabId},method,params);
function taskURL(value,host){const url=new URL(value);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('Choose an ordinary website.');if(url.origin===host&&url.pathname!=='/connect/sample')throw Error('Amplifier administration pages are excluded.');return url.href}
async function detachTask(){attachmentVersion++;const tabId=attachedTab;attachedTab=null;taskWorld=null;taskSnapshots.clear();if(tabId!==null)await chrome.debugger.detach({tabId}).catch(()=>{})}
async function attachTask(tabId,connection){
 if(!connection.grants.includes('browser.task'))throw Error('Enable tasks across pages from the extension connection page first.');
 const tab=await chrome.tabs.get(tabId);taskURL(tab.url,connection.host);
 await detachTask();await chrome.debugger.attach({tabId},'1.3');attachedTab=tabId;
}
async function taskFrame(target,connection){
 if(attachedTab!==target.tabId)throw Error('This tab is no longer attached. Choose it locally again.');
 const tree=await taskCommand(target.tabId,'Page.getFrameTree');const frame=tree.frameTree.frame;
 taskURL(frame.url,connection.host);
 const document=frame.id+':'+frame.loaderId;
 if(!taskWorld||taskWorld.document!==document){
  const world=await taskCommand(target.tabId,'Page.createIsolatedWorld',{frameId:frame.id,worldName:'amplifier-ui-use'});
  taskWorld={contextId:world.executionContextId,document};
 }
 return taskWorld;
}
async function taskEvaluate(target,frame,expression){
 const response=await taskCommand(target.tabId,'Runtime.evaluate',{contextId:frame.contextId,expression,returnByValue:true,awaitPromise:true});
 if(response.exceptionDetails||!response.result||!('value' in response.result))throw Error('The selected document did not return a confirmed result.');
 return response.result.value;
}
async function executeTask(connection,operation,target){
 if(!connection.grants.includes('browser.task')||attachedTab!==target.tabId)throw Error('Full-tab access is unavailable.');
 const action=operation.action, version=attachmentVersion;
 const current=()=>{if(pausedLocally||attachmentVersion!==version||attachedTab!==target.tabId||operation.deadline*1000<=Date.now())throw Error('The tab was stopped or delivery expired.');};
 current();
 if(action==='browser.task.open'){
  const url=taskURL(operation.args.url,connection.host);taskSnapshots.clear();
  current();const result=await taskCommand(target.tabId,'Page.navigate',{url});
  if(result.errorText)return {status:'blocked',reason:result.errorText};
  await new Promise(resolve=>setTimeout(resolve,400));
  return {status:'completed',url};
 }
 const frame=await taskFrame(target,connection);
 if(action==='browser.task.screenshot'){
  const sameDocument=async()=>{
   current();
   const latest=await taskFrame(target,connection);
   current();
   if(latest.document!==frame.document)throw Error('The document changed during screenshot capture. Observe it again.');
  };
  const metrics=await taskCommand(target.tabId,'Page.getLayoutMetrics'),v=metrics.cssVisualViewport;
  if(!Number.isFinite(v.clientWidth)||!Number.isFinite(v.clientHeight)||v.clientWidth<=0||v.clientHeight<=0)throw Error('The selected tab has no visible screenshot area.');
  const baseScale=Math.min(1,1280/v.clientWidth,900/v.clientHeight);
  for(const [quality,factor] of [[55,1],[40,.75],[25,.5]]){
   await sameDocument();const scale=baseScale*factor;
   const result=await taskCommand(target.tabId,'Page.captureScreenshot',{format:'jpeg',quality,clip:{x:v.pageX,y:v.pageY,width:v.clientWidth,height:v.clientHeight,scale},captureBeyondViewport:false});
   // Pixels are not bound to a CDP execution context. Discard them if the
   // selected document, URL permission, attachment or Stop changed in flight.
   await sameDocument();
   if(result.data.length<=240000)return {image:result.data,mimeType:'image/jpeg',width:Math.round(v.clientWidth*scale),height:Math.round(v.clientHeight*scale)};
  }
  return {status:'blocked',reason:'screenshot_size_limit',recovery:'This page could not fit in a bounded live image. View it directly on the connected device or retry after simplifying the page.'};
 }
 if(action==='browser.task.observe'){
  const state=await taskEvaluate(target,frame,'('+observeUI.toString()+')()');
  const revision=crypto.randomUUID();taskSnapshots.set(revision,{state,document:frame.document,at:Date.now()});
  while(taskSnapshots.size>16)taskSnapshots.delete(taskSnapshots.keys().next().value);
  return {revision,state};
 }
 if(action!=='browser.task.act')throw Error('Unsupported tab action.');
 const {revision,candidate,text}=operation.args,saved=taskSnapshots.get(revision);
 if(!saved)return {status:'stale',reason:'observation_absent'};
 if(saved.document!==frame.document)return {status:'stale',reason:'document_changed'};
 if(Date.now()-saved.at>15000)return {status:'stale',reason:'observation_expired'};
 const execute=(expected,candidate,text)=>{
  const registry=window[Symbol.for('amplifier.ui.observer.v1')];
  // CDP may reorder dictionary keys during returnByValue transport. Compare
  // canonical values while retaining every field and array order.
  const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
  if(!registry)return {status:'stale',reason:'observer_unavailable'};
  if(stable(registry.observe())!==stable(expected))return {status:'stale',reason:'observation_changed'};
  if(candidate==='wait')return {status:'completed'};
  if(candidate==='scroll_down'||candidate==='scroll_up'){window.scrollBy(0,(candidate==='scroll_down'?1:-1)*innerHeight*.75);return {status:'completed'}}
  const match=/^(e\d+):(click|type|select:(\d+))$/.exec(candidate);if(!match)return {status:'failed',reason:'invalid_candidate'};
  const el=registry.nodes.get(match[1]),state=registry.observe(),item=state.elements.find(e=>e.id===match[1]);
  if(!el||!el.isConnected||!item)return {status:'stale'};
  if(match[2]==='click'&&!item.editable&&item.tag!=='select')el.click();
  else if(match[2]==='type'&&item.editable){
   el.focus();if(el.isContentEditable)el.textContent=text;
   else{const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,text)}
   el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}));el.dispatchEvent(new Event('change',{bubbles:true}));
  }else if(match[3]!==undefined&&item.tag==='select'&&item.options[Number(match[3])]){el.value=item.options[Number(match[3])].value;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))}
  else return {status:'failed',reason:'invalid_candidate'};
  return {status:'completed'};
 };
 current();return taskEvaluate(target,frame,'('+execute.toString()+')('+[JSON.stringify(saved.state),JSON.stringify(candidate),JSON.stringify(text)].join(',')+')');
}
chrome.debugger.onDetach.addListener(source=>{if(source.tabId===attachedTab){attachmentVersion++;attachedTab=null;taskWorld=null;taskSnapshots.clear();stop()}});
