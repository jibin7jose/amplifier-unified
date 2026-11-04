importScripts('network.js','observed-dom.js','task-browser.js');
const instanceId=crypto.randomUUID();
let busy=false, timer=null, lastLaunch=0;
let localStopVersion=0, pausedLocally=false;
const transientReceiptLifetime=60000;
chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
chrome.storage.session.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
chrome.alarms.create('connect',{periodInMinutes:0.5});

function validHost(value){
 const url=new URL(value);
 if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||
    !(url.protocol==='https:'||(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname))))throw Error('Use a trusted HTTPS service address.');
 return url.origin;
}
async function state(){return(await chrome.storage.local.get('connection')).connection}
async function store(value){await chrome.storage.local.set({connection:value})}
async function clearTransientReceipt(){
 await chrome.storage.session.remove('pendingReceipt');
 // Remove a read-only receipt left by an earlier extension version. Missing
 // retention is effectful/legacy and must remain available for recovery.
 const old=(await chrome.storage.local.get('pendingReceipt')).pendingReceipt;
 if(old?.retention==='transient')await chrome.storage.local.remove('pendingReceipt');
}
async function pendingReceipt(connection){
 let durable=(await chrome.storage.local.get('pendingReceipt')).pendingReceipt;
 if(durable?.retention==='transient'){await chrome.storage.local.remove('pendingReceipt');durable=null;}
 let transient=(await chrome.storage.session.get('pendingReceipt')).pendingReceipt;
 if(transient){
  const now=Date.now();
  if(transient.retention!=='transient'||!Number.isFinite(transient.createdAt)||!Number.isFinite(transient.expiresAt)||
     transient.createdAt>now||transient.expiresAt<=now||transient.expiresAt-transient.createdAt>transientReceiptLifetime||
     !connection?.token||connection.revoked||connection.stopped||pausedLocally||
     transient.host!==connection.host||transient.deviceID!==connection.deviceID){
   await chrome.storage.session.remove('pendingReceipt');transient=null;
  }
 }
 return durable||transient;
}
async function clearReceipt(receipt){
 const area=chrome.storage[receipt.retention==='transient'?'session':'local'];
 const saved=(await area.get('pendingReceipt')).pendingReceipt;
 if(saved&&['host','deviceID','id','lease'].every(key=>saved[key]===receipt[key]))await area.remove('pendingReceipt');
}
async function api(connection,path,payload,anonymous=false){
 return connectionRequest(connection.host,path,payload,anonymous?undefined:connection.token);
}
async function checkConnection(){
 const connection=await state();
 if(!connection?.token||connection.revoked)throw Error('Connect the extension first.');
 try{
  const heartbeat=await api(connection,'/self/heartbeat',{instanceId,targets:await advertise(connection),setupId:connection.setupId});
  const current=await state();
  if(current?.token!==connection.token||current?.host!==connection.host)throw Error('The connection changed. Retry with the current connection.');
  await store({...current,grants:heartbeat.grants,lastSeen:Date.now(),error:null,errorCode:null});
  resume();return {connected:true};
 }catch(error){
  const current=await state();
  if(current?.token===connection.token&&current?.host===connection.host){
   if(error.status===401){await clearTransientReceipt();await detachTask();}
   await store({...current,error:error.message,errorCode:error.code||null,revoked:current.revoked||error.status===401});
  }
  throw error;
 }
}
async function advertise(connection){
 const targets=[{id:'connection',revision:instanceId,label:'Browser extension connection',capabilities:['connection.ping'],ready:true}];
 if(!connection.stopped&&connection.target&&connection.grants.includes('browser.observe')){
  try{
   const target=connection.target;
   if(target.mode==='task'){
    if(attachedTab===target.tabId){await taskFrame(target,connection);targets.push({id:target.id,revision:target.documentId,label:target.label,capabilities:connection.grants.filter(g=>g.startsWith('browser.')),ready:true})}
    return targets;
   }
   const result=await chrome.scripting.executeScript({target:{tabId:target.tabId,documentIds:[target.documentId]},func:()=>({url:location.href})});
   if(result[0]?.documentId===target.documentId){
    targets.push({id:target.id,revision:target.documentId,label:target.label,capabilities:connection.grants.filter(g=>g.startsWith('browser.')&&g!=='browser.task'),ready:true});
   }
  }catch{}
 }
 return targets;
}
async function execute(connection,operation){
 if(operation.deadline*1000<=Date.now())throw Error('Delivery expired before execution.');
 if(operation.action==='connection.ping')return {message:'Connection confirmed by the browser extension.'};
 const fresh=await state(),target=fresh?.target;
 if(pausedLocally||fresh?.host!==connection.host||fresh?.token!==connection.token||fresh.stopped||!target||
    target.id!==operation.targetId||target.documentId!==operation.revision)throw Error('The selected browser document changed or was stopped.');
 if(operation.action.startsWith('browser.task.')){
  if(target.mode!=='task')throw Error('Select a tab for tasks across pages locally first.');
  return executeTask(fresh,operation,target);
 }
 // documentIds targets a browser-owned document identity, not a reused tab ID.
 const result=await chrome.scripting.executeScript({
  target:{tabId:target.tabId,documentIds:[target.documentId]},
  func:(action,args)=>{
   if(action==='browser.observe'){
    const controls=[...document.querySelectorAll('button,input,select,textarea,a[href]')].filter(item=>{
     const box=item.getBoundingClientRect(),style=getComputedStyle(item);
     return box.width>0&&box.height>0&&style.visibility!=='hidden'&&style.display!=='none'&&
      !item.closest('[inert],[aria-hidden="true"]')&&!['password','file','hidden'].includes(item.type);
    }).slice(0,60).map(item=>({
     tag:item.tagName.toLowerCase(),type:item.type||'',label:(item.innerText||item.getAttribute('aria-label')||item.placeholder||item.name||'').slice(0,100),
     selector:item.id?'#'+CSS.escape(item.id):null,
     checked:item.tagName==='INPUT'&&['checkbox','radio'].includes(item.type)?item.checked:
      ['checkbox','radio'].includes(item.getAttribute('role'))?
       item.getAttribute('aria-checked')==='true'?true:item.getAttribute('aria-checked')==='false'?false:
        item.getAttribute('role')==='checkbox'&&item.getAttribute('aria-checked')==='mixed'?'mixed':null:null,
     indeterminate:item.tagName==='INPUT'&&item.type==='checkbox'?item.indeterminate:false,
     ...(['password','file','hidden'].includes(item.type)?{}:{value:(item.value||'').slice(0,200)})
    }));
    return {url:location.href,title:document.title,text:document.body.innerText.slice(0,7000),controls};
   }
   const items=document.querySelectorAll(args.selector);
   if(items.length!==1)throw Error('The selector must identify exactly one element.');
   const item=items[0];
   if(!item.getClientRects().length||item.disabled||['password','file','hidden'].includes(item.type))throw Error('This element is not available for this action.');
   if(action==='browser.click'){
    if(!item.matches('button,a,input[type=button],input[type=submit],input[type=radio],input[type=checkbox],[role=button]'))throw Error('Choose an interactive control.');
    item.click();
    return {message:'Clicked the selected control.'};
   }
   if(action==='browser.type'){
    if(!item.matches('input,textarea')||item.readOnly)throw Error('Choose an editable text field.');
    const prototype=item instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype,'value').set.call(item,args.text);
    item.dispatchEvent(new Event('input',{bubbles:true}));item.dispatchEvent(new Event('change',{bubbles:true}));
    return {message:'Entered text in the selected field.'};
   }
   throw Error('Unsupported browser action.');
  },args:[operation.action,operation.args],
 });
 if(result.length!==1||result[0].documentId!==target.documentId||!result[0].result)throw Error('No confirmed result from the selected document.');
 return result[0].result;
}
async function poll(){
 clearTimeout(timer);
 try{
  let connection=await state();
  if(!connection?.token||connection.revoked){await clearTransientReceipt();await detachTask();return;}
  if(connection.rotationId){
   const rotated=await api(connection,'/self/rotate',{rotationId:connection.rotationId});
   connection={...connection,token:rotated.token};delete connection.rotationId;
   await store(connection);
  }
  const targets=await advertise(connection);
  const heartbeat=await api(connection,'/self/heartbeat',{instanceId,targets,setupId:connection.setupId});
  // Avoid overwriting a local Stop/target change with an earlier snapshot.
  let current=await state();
  if(current?.token===connection.token){
   current={...current,grants:heartbeat.grants,lastSeen:Date.now(),error:null,errorCode:null};
   await store(current);connection=current;
  }else return;
  await chrome.action.setBadgeText({text:targets.length>1?'ON':''});
  const delivery=await api(connection,'/self/poll',{});
  if(delivery.operation){
   const operation=delivery.operation;
   const stopVersion=localStopVersion;
   let status='completed',result;
   try{
    result=await execute(connection,operation);
    if(!result||typeof result!=='object'||Array.isArray(result))throw Error('The selected document returned an invalid result. Inspect the current page before continuing.');
    if(new TextEncoder().encode(JSON.stringify(result)).length>262144)throw Error('The operation result is too large to send. Any input may already have happened; inspect the current page before continuing.');
   }catch(error){status='failed';result={message:String(error.message).slice(0,1000)}}
   const receipt={id:operation.id,lease:operation.lease,status,result};
   const pending={host:connection.host,deviceID:connection.deviceID,retention:operation.retention,...receipt};
   if(operation.retention==='transient'){
    if(pausedLocally||stopVersion!==localStopVersion)return;
    pending.createdAt=Date.now();pending.expiresAt=pending.createdAt+transientReceiptLifetime;
    // Observations never enter durable extension storage. Session storage
    // survives worker suspension, but not browser restart or extension reload.
    await chrome.storage.session.set({pendingReceipt:pending});
    chrome.alarms.create('receipt-expiry',{when:pending.expiresAt});
    const current=await pendingReceipt(await state());
    if(stopVersion!==localStopVersion||current?.id!==pending.id){await clearTransientReceipt();return;}
   }else{
    // Persist effectful receipts before delivery; recovery retries receipts,
    // never browser actions.
    await chrome.storage.local.set({pendingReceipt:pending});
   }
   await api(connection,'/self/result',receipt);
   await clearReceipt(pending);
  }
 }catch(error){
  const current=await state();
  if(current){
   const revoked=error.status===401;if(revoked){await clearTransientReceipt();await detachTask();}
   await store({...current,error:revoked?'Access was removed. Connect again explicitly.':error.message,errorCode:error.code||null,revoked:current.revoked||revoked});
   await chrome.action.setBadgeText({text:revoked?'OFF':'…'});
  }
 }finally{
  const current=await state();
  if(current?.token&&!current.revoked)timer=setTimeout(resume,1000);
 }
}
async function resume(){
 if(busy)return;
 // Claim recovery and polling together, before the first asynchronous read.
 busy=true;
 try{
 const connection=await state(),pending=await pendingReceipt(connection);
 if(connection?.token&&!connection.revoked&&pending){
  if(pending.host!==connection.host||pending.deviceID!==connection.deviceID){
   await store({...connection,error:'A result from an earlier connection still needs recovery. Do not repeat its action.'});
   return;
  }else{
   try{await api(connection,'/self/result',{id:pending.id,lease:pending.lease,status:pending.status,result:pending.result});await clearReceipt(pending)}
   catch(error){
    // Read-only deliveries can expire or be evicted from the host's bounded
    // view cache. Discard only their receipt; never execute the action again.
    if(pending.retention==='transient'&&[404,410].includes(error.status))await clearReceipt(pending);
    else{if(error.status===401){await clearTransientReceipt();await detachTask();await store({...connection,revoked:true,error:'Access was removed.'});}else{clearTimeout(timer);timer=setTimeout(resume,5000)}return}
   }
  }
 }
 return await poll();
 }finally{busy=false}
}
async function useTab(tabId,taskMode=false){
 const stopVersion=localStopVersion;
 const connection=await state();
 if(!connection?.token||connection.revoked)throw Error('Connect the extension first.');
 if(!connection.grants.includes('browser.observe'))throw Error('Enable browser access from the connection page first.');
 // The popup supplies its own window's active tab. A background worker has
 // no window and must not infer a different one after asynchronous work.
 if(!Number.isInteger(tabId)||tabId<0)throw Error('Open the extension from the tab you want to use.');
 const tab=await chrome.tabs.get(tabId);
 if(!tab.active||!tab.url)throw Error('Return to the intended tab and choose Use this tab again.');
 const url=new URL(tab.url);
 if(!['https:','http:'].includes(url.protocol))throw Error('Choose an ordinary web page.');
 if(url.origin===connection.host&&url.pathname!=='/connect/sample')throw Error('Choose the sample or another website. Amplifier’s approval and administration pages are excluded.');
 if(taskMode)await attachTask(tabId,connection);else await detachTask();
 const rows=taskMode?[{documentId:crypto.randomUUID(),result:{title:tab.title,url:tab.url}}]:await chrome.scripting.executeScript({target:{tabId:tab.id,frameIds:[0]},func:()=>({title:document.title,url:location.href})});
 const documentId=rows[0]?.documentId;
 if(!documentId)throw Error('The browser did not supply an exact document identity.');
 const target={id:crypto.randomUUID(),tabId:tab.id,documentId,mode:taskMode?'task':'document',label:(rows[0].result.title||url.hostname).slice(0,140)};
 const current=await state();
 if(stopVersion!==localStopVersion||current?.token!==connection.token){await detachTask();throw Error('Selection was stopped before it became ready.');}
 pausedLocally=false;
 await store({...current,target,stopped:false,error:null});
 // A poll may be waiting; advertise immediately so setup does not wait for it.
 await checkConnection();
 resume();
 return {selected:target.label};
}
async function stop(){
 // Set the in-process stop before awaiting storage or network. An earlier
 // asynchronous selection must never turn execution back on after this.
 localStopVersion++;pausedLocally=true;
 await clearTransientReceipt();
 const connection=await state();
 if(!connection)return {};
 await store({...connection,stopped:true,target:null});
 await detachTask();
 await chrome.action.setBadgeText({text:''});
 try{await api(connection,'/self/stop',{})}catch{}
 return {stopped:true};
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
 if(sender.id!==chrome.runtime.id||!sender.url?.startsWith(chrome.runtime.getURL('')))return;
 (async()=>{
  if(message.type==='state'){
   const value=await state();
   return value?{host:value.host,deviceID:value.deviceID,grants:value.grants,stopped:value.stopped,target:value.target?.mode==='task'&&attachedTab!==value.target.tabId?null:value.target?.label,revoked:value.revoked,error:value.error,errorCode:value.errorCode,lastSeen:value.lastSeen}:{};
  }
  if(message.type==='check-connection')return checkConnection();
  if(message.type==='use-tab')return useTab(message.tabId);
  if(message.type==='use-task-tab')return useTab(message.tabId,true);
  if(message.type==='stop')return stop();
  if(message.type==='resume'){resume();return {started:true}}
  throw Error('Unsupported local action.');
 })().then(reply,error=>reply({error:error.message,code:error.code||null}));
 return true;
});
chrome.runtime.onMessageExternal.addListener((message,sender,reply)=>{
 try{
  if(message.type!=='open-connect'||!sender.url||Date.now()-lastLaunch<1000)throw Error('Unsupported request.');
  const host=validHost(new URL(sender.url).origin);
  if(!/^[A-Za-z0-9_-]{32}$/.test(message.setupId))throw Error('Invalid setup context.');
  lastLaunch=Date.now();
  // Chrome can carry the initiating web button's gesture into this handler.
  // When supported, request host access here and avoid another Connect button.
  // Other clients/browser policies retain the explicit in-app gesture path.
  const open=auto=>chrome.tabs.create({url:chrome.runtime.getURL('connect.html')+'?'+new URLSearchParams({host,setup:message.setupId,...(auto?{auto:'1'}:{})})});
  chrome.permissions.request({origins:[host+'/*']}).then(async allowed=>{
   if(!allowed){reply({error:'Permission to reach this service was not granted.'});return}
   await open(true);reply({opened:true});
  },async()=>{await open(false);reply({opened:true})}).catch(error=>reply({error:error.message}));
 }catch(error){reply({error:error.message})}
 return true;
});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='connect')return resume();if(alarm.name==='receipt-expiry')return state().then(pendingReceipt)});
chrome.runtime.onStartup.addListener(()=>resume());
resume();
