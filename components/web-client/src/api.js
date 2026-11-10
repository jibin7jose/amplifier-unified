import {createDraftJournal} from './draft-journal.js';
import {downloadBlobContent} from './appearance-library.js';
// A page owns one presentation identity. Reload/duplicate restores a copy of
// the previous view, never the same writable client as another live tab.
export const clientId=crypto.randomUUID();
export const draftJournal=createDraftJournal(clientId);
let attachment,journalResumed=false;
let authenticationScope=null,authenticationGeneration=0,authenticationExpired=false;
const authenticationListeners=new Set();
export function onAuthenticationChange(listener){authenticationListeners.add(listener);return()=>authenticationListeners.delete(listener)}
function observeAuthentication(res,generation){
  const scope=res.headers?.get?.('X-Amplifier-History-Scope');
  if(generation!==authenticationGeneration)return null;
  const expired=res.status===401&&!authenticationExpired;
  const replaced=!!scope&&!!authenticationScope&&scope!==authenticationScope;
  if(expired||replaced){
    authenticationGeneration++;
    for(const listener of authenticationListeners)listener(res.status===401?null:scope);
  }
  if(res.status===401){authenticationScope=null;authenticationExpired=true}
  else if(scope){authenticationScope=scope;authenticationExpired=false}
  return scope;
}
export function clientUrl(path){
  if(!path.startsWith('/api/'))return path;
  const url=new URL(path,location.origin);url.searchParams.set('clientId',clientId);
  return url.pathname+url.search;
}
export function attachClient(signal){
  if(!attachment){
    let resumeClientId;try{resumeClientId=sessionStorage.getItem('amplifier.clientId')||undefined}catch{}
    attachment=request('/api/clients/attach',{method:'POST',signal,body:{clientId,resumeClientId,kind:'web',protocolVersion:1}}).then(async result=>{
      if(!journalResumed){draftJournal.resume(resumeClientId);journalResumed=true}
      try{sessionStorage.setItem('amplifier.clientId',clientId)}catch{}
      // Restore into this new private view before opening its state stream. No
      // message is submitted and no runtime/conversation action is replayed.
      for(const entry of draftJournal.entries()){
        try{await request('/api/actions',{method:'POST',signal,body:{action:'view.update',args:{sessionId:entry.sessionId,patch:{draft:entry.text,draftDocument:entry.document??null}}}})}
        catch(error){if(error.status!==404)throw error;continue} // Keep missing-chat text locally; never block opening the app.
        draftJournal.acknowledge(entry);
      }
      return result;
    }).catch(error=>{attachment=null;throw error});
  }
  return attachment;
}

// Host response time anchors live elapsed displays even when a device clock differs.
let hostClock;
export function hostNow(){return hostClock?hostClock.seconds+(performance.now()-hostClock.received)/1000:Date.now()/1000}
const actionTimings=[];
export function recordActionTiming(action,queuedAt,startedAt){
 if(!/^[a-z][a-z0-9_.]{0,79}$/.test(action))return;
 actionTimings.push({action,at:Date.now()/1000,queueMs:Math.round(startedAt-queuedAt),runMs:Math.round(performance.now()-startedAt)});
 if(actionTimings.length>30)actionTimings.shift();
}
export function recentActionTimings(){return actionTimings.map(row=>({...row}))}
const requestTimings=[];
export function recentRequestTimings(){return requestTimings.map(row=>({...row}))}
export async function request(path, options = {}) {
  const started=performance.now(),row={at:Date.now()/1000,method:options.method||'GET',route:path.split('?')[0].replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/ig,':id')};
  const action=options.body?.action;if(typeof action==='string'&&/^[a-z][a-z0-9_.]{0,79}$/.test(action))row.action=action;
  try{return await measuredRequest(path,options,row)}
  catch(error){row.error=error.name;error.requestPath=path.split('?')[0];throw error}
  finally{row.elapsedMs=Math.round(performance.now()-started);requestTimings.push(row);if(requestTimings.length>30)requestTimings.shift()}
}
async function measuredRequest(path, options, timing) {
  const authenticationAtStart=authenticationGeneration;
  const headers = { ...(path.startsWith('/api/')?{'X-Amplifier-Client':clientId,'X-Amplifier-State-Transport':'delta-v1'}:{}), ...options.headers };
  const mutation=!['GET','HEAD'].includes((options.method||'GET').toUpperCase());
  let body = options.body;
  if (body && typeof body === 'object' && !(body instanceof FormData)) { headers['Content-Type']='application/json'; body=JSON.stringify(body); }
  const unavailable=error=>{
    if(error?.name==='AbortError')return error;
    return Object.assign(new Error('The connection to Amplifier was interrupted.'),{code:'transport_unavailable',unconfirmed:mutation,cause:error});
  };
  let res;
  try{res=await fetch(path,{...options,body,headers,credentials:'same-origin'})}catch(error){throw unavailable(error)}
  const historyScope=observeAuthentication(res,authenticationAtStart);
  timing.status=res.status;timing.requestId=res.headers?.get?.('X-Amplifier-Request-Id')||undefined;
  const server=/app;dur=([0-9.]+)/.exec(res.headers?.get?.('Server-Timing')||'');if(server)timing.serverMs=Number(server[1]);
  const dated=Date.parse(res.headers?.get?.('date'));
  // Second-precision or delayed responses cannot wind an active timer back.
  if(Number.isFinite(dated)&&(!hostClock||dated/1000>hostNow()))hostClock={seconds:dated/1000,received:performance.now()};
  let content;try{content=await res.text()}catch(error){throw unavailable(error)}
  let data;
  const invalidResponse=()=>Object.assign(new Error(`The server returned an unexpected response (${res.status}).`),{code:'invalid_response',status:res.status,unconfirmed:mutation});
  try { data=content?JSON.parse(content):{}; } catch { throw invalidResponse(); }
  // An unreadable action acknowledgement proves neither acceptance nor refusal.
  // Preserve structured server errors; don't infer nonexecution from proxy bytes.
  const actionAck=mutation&&path.split('?')[0]==='/api/actions';
  if(actionAck&&(!data||typeof data!=='object'||Array.isArray(data)))throw invalidResponse();
  const readableError=[data?.error,data?.error?.message,data?.message].some(value=>typeof value==='string'&&value.trim());
  if(actionAck&&!res.ok&&data.accepted!==false&&!readableError)throw invalidResponse();
  // A repeated command can return its original rejection inside an HTTP 200 receipt.
  const rejectionStatus=res.ok&&data.accepted===false?(Number.isInteger(data.status)&&data.status>=400&&data.status<600?data.status:400):res.status;
  if(!res.ok || data.accepted===false) throw Object.assign(new Error(typeof data.error==='string'?data.error:data.error?.message||data.message||`Request failed (${rejectionStatus})`),{code:data.code,state:data.state,receipt:data.receipt,status:rejectionStatus});
  if(actionAck&&data.accepted!==true)throw invalidResponse();
  if(historyScope&&data.state?.client?.kind==='web')data.state={...data.state,client:{...data.state.client,historyScope}};
  return data;
}
export function download(filename, content, type='application/json') {
  const blob=new Blob([downloadBlobContent(content,type)],{type});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
// Repeated browser observations describe client-owned UI. Conversation text and
// elapsed progress already have an authoritative, session-scoped server view.
// Both automatic reports and local rendered-view inspection exclude private input.
const privateSelector='[data-private="true"],input[type="password"]';
const privateElement=el=>!!el?.closest(privateSelector);
function observedText(root,interfaceOnly=false){
  if(privateElement(root))return "";
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,{acceptNode(node){
    if(node.nodeType===Node.ELEMENT_NODE){const style=getComputedStyle(node);return privateElement(node)||(interfaceOnly&&node.dataset.viewSource)||style.display==='none'||style.visibility==='hidden'?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_SKIP;}
    return node.textContent.trim()?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_SKIP;
  }});
  let node,text='';
  while((node=walker.nextNode())&&text.length<60000)text+=node.textContent.trim()+'\n';
  return text.slice(0,60000);
}
function observedSelection(root){
  const selection=window.getSelection();
  if(!selection?.rangeCount||!root.contains(selection.anchorNode)||!root.contains(selection.focusNode))return '';
  // A range may start and end outside a private editor while crossing it.
  for(let i=0;i<selection.rangeCount;i++){
    const range=selection.getRangeAt(i);
    if([...root.querySelectorAll(privateSelector)].some(el=>range.intersectsNode(el)))return '';
  }
  return selection.toString().slice(0,10000);
}
export function visibleView(root, clientId, {interfaceOnly=false}={}) {
  if(!root)return {clientId};
  const visible=el=>!!el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden';
  const controls=[...root.querySelectorAll('button,input,textarea,select,a,[role=separator],[role=textbox]')].filter(visible).map(el=>({id:el.id||null,action:el.dataset.action||el.closest('[data-action]')?.dataset.action||null,...(el.closest('[data-shell-component]')?{component:{instanceId:el.closest('[data-shell-component]').dataset.shellComponent,slot:el.closest('[data-shell-component]').dataset.shellSlot,command:el.dataset.shellCommand||null}}:{}),label:privateElement(el)?(el.dataset.viewLabel||'[private control]'):el.dataset.viewLabel||el.getAttribute('aria-label')||el.labels?.[0]?.textContent||observedText(el).trim().slice(0,160),type:el.getAttribute('role')||el.type||el.tagName.toLowerCase(),...(el.getAttribute('role')==='separator'?{minimum:Number(el.getAttribute('aria-valuemin')),maximum:Number(el.getAttribute('aria-valuemax')),current:Number(el.getAttribute('aria-valuenow'))}:{}),value:privateElement(el)?'[redacted]':el.isContentEditable?el.textContent:el.value,disabled:el.getAttribute('aria-disabled')==='true'||!!el.disabled||!!el.closest('[inert]'),...(el.hasAttribute('aria-expanded')?{expanded:el.getAttribute('aria-expanded')==='true'}:{}),focused:document.activeElement===el}));
  const panes=Object.fromEntries(['navigation','conversation','canvas'].flatMap(name=>{const el=root.querySelector(`[data-part="${name}"]`);if(!el)return [];const box=el.getBoundingClientRect();return [[name,{x:Math.round(box.x),y:Math.round(box.y),width:Math.round(box.width),height:Math.round(box.height),visible:visible(el),interactive:visible(el)&&!el.closest('[inert]')}]]}));
  return {clientId,panes,webApp:{standalone:!!(window.matchMedia?.('(display-mode: standalone)').matches||window.matchMedia?.('(display-mode: window-controls-overlay)').matches||navigator.standalone),secureContext:window.isSecureContext,serviceWorkerControlled:!!navigator.serviceWorker?.controller,online:navigator.onLine},visibleText:observedText(root,interfaceOnly),...(interfaceOnly?{visibleTextScope:'interface',conversationSource:'Selected session messages, execution and activity in app state'}:{}),controls,selectedText:observedSelection(root),viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY},url:location.pathname};
}
