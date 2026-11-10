import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {expect} from '@playwright/test';

// Deliberately allowlist navigation, never editor contents, credentials, headers,
// complete request bodies, or complete state frames in the retained evidence.
function navigation(view={}){
 const editor=view.providerEditor||{};
 return {panel:view.panel??null,settingsSection:view.settingsSection??null,
  settingsExpanded:view.settingsExpanded??[],
  providerEditor:{id:editor.id??null,detailOpen:editor.detailOpen===true,
   orderOpen:editor.orderOpen??null,orderPresent:!!editor.order}};
}
function stateSummary(state){
 return {clientId:state.client?.id,hostInstanceId:state.client?.hostInstanceId,
  revision:state.revision,view:navigation(state.view)};
}
function patchSummary(patch={}){
 const safe=navigation(patch);
 return Object.fromEntries(Object.entries(safe).filter(([key])=>Object.hasOwn(patch,key)));
}
const detailPatch=row=>row.patch?.providerEditor?.id==='two'&&row.patch.providerEditor.detailOpen;
const listPatch=row=>row.patch?.settingsExpanded?.includes('providers')&&row.patch.providerEditor?.detailOpen===false;
const marker=stage=>'[settings-reload:'+stage+']';
async function boundedRead(promise){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Diagnostic operation unavailable')),2000)})])}
 finally{clearTimeout(timer)}
}
async function boundedJSON(response){
 const limit=524288;
 if(Number(response.headers()['content-length'])>limit)throw Error('Diagnostic body exceeds limit');
 // Playwright buffers body() before returning it. This bounds parsing, not
 // streaming acquisition memory; missing/incorrect Content-Length remains a limit.
 let bytes;
 try{bytes=await boundedRead(response.body())}catch{throw Error('Diagnostic body unavailable')}
 if(bytes.length>limit)throw Error('Diagnostic body exceeds limit');
 try{return JSON.parse(bytes.toString('utf8'))}catch{throw Error('Diagnostic JSON unavailable')}
}
function assertProviderRoute(state,detail,stage){
 const message=marker(stage),view=state.view;
 assert.equal(view.panel,'settings',message+' panel');
 assert.equal(view.settingsSection,'setup',message+' section');
 assert.deepEqual(view.settingsExpanded,['providers'],message+' destination');
 assert.equal(view.providerEditor.detailOpen,detail,message+' detailOpen');
 assert.equal(view.providerEditor.orderPresent,false,message+' unexpected order');
 if(detail)assert.equal(view.providerEditor.id,'two',message+' provider identity');
}

// This listener is installed in the native socket constructor BEFORE the app
// assigns onmessage (state-stream.js). It only observes; frames and dispatch are
// unchanged. MutationObserver records the first actual Settings DOM separately.
function installReloadObservation(){
 if(window!==window.top)return;
 const documentId=crypto.randomUUID();
 const historySummary=()=>{
  const value=history.state?.amplifierSettings;
  return {historyLength:history.length,historyMarker:value?{token:value.token,index:value.index}:null};
 };
 const evidence={documentId,resumeClientId:sessionStorage.getItem('amplifier.clientId'),
  initialHistory:historySummary(),firstState:null,firstDOM:null,domChanges:[]};
 window.__settingsReloadEvidence=evidence;
 const report=(kind,value)=>{window.__settingsReloadReport({documentId,kind,value}).catch(()=>{})};
 report('boot',{resumeClientId:evidence.resumeClientId,...evidence.initialHistory});
 const Native=window.WebSocket;
 window.WebSocket=class extends Native{
  constructor(...args){
   super(...args);
   const url=new URL(args[0],location.href);
   if(url.pathname!=='/api/events/ws')return;
   this.addEventListener('message',event=>{
    if(evidence.firstState)return;
    try{
     const frame=JSON.parse(event.data);if(frame.event!=='state')return;
     const state=frame.data,view=state.view||{},editor=view.providerEditor||{};
     evidence.firstState={socketClientId:url.searchParams.get('clientId'),
      clientId:state.client?.id,hostInstanceId:state.client?.hostInstanceId,revision:state.revision,
      view:{panel:view.panel??null,settingsSection:view.settingsSection??null,
       settingsExpanded:view.settingsExpanded??[],providerEditor:{id:editor.id??null,
        detailOpen:editor.detailOpen===true,orderOpen:editor.orderOpen??null,orderPresent:!!editor.order}},
      history:historySummary()};
     report('firstState',evidence.firstState);
    }catch{report('frameError',{invalidStateFrame:true})}
   });
  }
 };
 let previous;
 const observer=new MutationObserver(()=>{
  const root=document.querySelector('.a-settings-experience');if(!root)return;
  const value={route:root.dataset.settingsRoute,page:root.dataset.settingsPage,
   compact:root.dataset.compact,...historySummary()};
  const key=JSON.stringify(value);if(key===previous)return;previous=key;
  if(!evidence.firstDOM){evidence.firstDOM=value;report('firstDOM',value)}
  if(evidence.domChanges.length<32){evidence.domChanges.push(value);report('dom',value)}
 });
 observer.observe(document,{subtree:true,childList:true,attributes:true,
  attributeFilter:['data-settings-route','data-settings-page','data-compact']});
}

export async function createSettingsReloadObservation(page,directory){
 const evidence={version:1,commands:[],attachments:[],documents:[],confirmed:{},pending:{},cleanup:[],dropped:0,
  limits:{responseBytes:524288,operationDeadlineMs:2000,bodyAcquisition:'Content-Length checked when present; Playwright body() buffers before actual-size check. No hard streaming-memory bound.',privateBodiesRetained:false}};
 const requests=new WeakMap(),responses=new WeakMap(),reading=new Set();
 let sequence=0,stage='installed',disposed=false;
 const root=page.locator('.a-settings-experience');
 const append=(rows,value)=>{if(rows.length<160)rows.push(value);else evidence.dropped++;return value};
 const onRequest=request=>{
  if(requests.has(request))return;
  if(request.method()!=='POST')return;
  const path=new URL(request.url()).pathname;
  if(!['/api/actions','/api/clients/attach'].includes(path))return;
  const body=request.postDataJSON(),clientId=request.headers()['x-amplifier-client'];
  if(path==='/api/clients/attach'){
   const row=append(evidence.attachments,{sequence:++sequence,stage,clientId:body.clientId,resumeClientId:body.resumeClientId});
   requests.set(request,row);return;
  }
  const row=append(evidence.commands,{sequence:++sequence,stage,commandId:body.id,clientId,action:body.action,
   ...(body.action==='view.update'?{patch:patchSummary(body.args?.patch)}:{})});
  requests.set(request,row);
 };
 const readResponse=response=>{
  if(responses.has(response))return responses.get(response);
  const row=requests.get(response.request());if(!row)return Promise.resolve();
  const work=(async()=>{
   row.httpStatus=response.status();
   try{
    const receipt=await boundedJSON(response);
    row.receipt={accepted:receipt.accepted,revision:receipt.revision,stateRevision:receipt.stateRevision,
     hostInstanceId:receipt.hostInstanceId,...(receipt.clientId?{clientId:receipt.clientId}:{})};
   }catch{row.receiptUnreadable=true}
  })();
  responses.set(response,work);reading.add(work);work.finally(()=>reading.delete(work));
  return work;
 };
 const onResponse=response=>{void readResponse(response)};
 const onFailed=request=>{
  const row=requests.get(request);if(!row)return;
  const reason=request.failure()?.errorText;
  row.transportFailure=/^net::[A-Z_]+$/.test(reason||'')?reason:'request-failed';
 };
 page.on('request',onRequest);page.on('response',onResponse);page.on('requestfailed',onFailed);
 await page.exposeFunction('__settingsReloadReport',event=>{
  let document=evidence.documents.find(row=>row.documentId===event.documentId);
  if(!document)document=append(evidence.documents,{documentId:event.documentId,stage,dom:[]});
  if(event.kind==='dom'){if(document.dom.length<32)document.dom.push(event.value)}
  else document[event.kind]=event.value;
 });
 await page.addInitScript(installReloadObservation);
 const presentation=()=>page.evaluate(()=>{
  const root=document.querySelector('.a-settings-experience'),value=history.state?.amplifierSettings;
  return {clientId:window.amplifier?.shellClientId,storedClientId:sessionStorage.getItem('amplifier.clientId'),
   route:root?.dataset.settingsRoute,page:root?.dataset.settingsPage,historyLength:history.length,
   historyMarker:value?{token:value.token,index:value.index}:null};
 });
 async function command(perform,matches,clientId){
  assert.ok(typeof clientId==='string'&&clientId.length>0,marker(stage)+' missing browser client identity');
  const after=sequence;
  const waiting=page.waitForResponse(response=>{
   const row=requests.get(response.request());
   return row?.sequence>after&&row.clientId===clientId&&row.action==='view.update'&&matches(row);
  });
  // A failed UI assertion must not leave a later timeout as an unhandled rejection.
  waiting.catch(()=>{});
  await perform();
  let response;
  try{response=await waiting}catch{throw Error(marker(stage)+' matching navigation receipt unavailable')}
  await readResponse(response);
  const row=requests.get(response.request()),message=marker(stage);
  assert.ok(row.commandId,message+' missing actual wire command ID');
  assert.equal(response.ok(),true,message+' HTTP rejected');
  assert.equal(row.receipt?.accepted,true,message+' command not accepted');
  assert.ok(Number.isInteger(row.receipt.stateRevision),message+' missing authoritative revision');
  assert.ok(row.receipt.hostInstanceId,message+' missing host identity');
  return row;
 }
 async function readClient(clientId,label){
  assert.ok(typeof clientId==='string'&&clientId.length>0,marker(label)+' missing state GET client identity');
  // page.request inherits the fixture's public bearer authentication. Binding is
  // explicit: a global GET or optimistic window.amplifier.getState is not proof.
  const response=await page.request.get(new URL('/api/state',page.url()).href,{
   headers:{'X-Amplifier-Client':clientId,'X-Amplifier-State-Transport':'delta-v1'}});
  let primary;
  try{
   assert.equal(response.ok(),true,marker(label)+' state GET rejected');
   const value=stateSummary(await boundedJSON(response));
   assert.equal(value.clientId,clientId,marker(label)+' wrong state GET client');
   return value;
  }catch(error){primary=error;throw error}
  finally{
   try{await boundedRead(response.dispose());evidence.cleanup.push({stage:label,operation:'state-response-dispose',status:'completed'})}
   catch{
    evidence.cleanup.push({stage:label,operation:'state-response-dispose',status:'unavailable',primaryPreserved:!!primary});
    if(!primary)throw Error(marker(label)+' state response cleanup unavailable');
   }
  }
 }
 function assertConfirmed(state,row,label){
  assert.equal(state.hostInstanceId,row.receipt.hostInstanceId,marker(label)+' host changed');
  assert.ok(state.revision>=row.receipt.stateRevision,marker(label)+' state predates receipt');
 }
 async function reload(record,before,confirmed,detail,depth){
  stage=detail?'confirmed:reload':'pending:reload';
  const after=sequence;record.dialogs=[];
  const dialogWork=[];
  const onDialog=dialog=>{
   const row={type:dialog.type(),accepted:false};record.dialogs.push(row);
   dialogWork.push((async()=>{
    if(row.type==='beforeunload'){await dialog.accept();row.accepted=true}
    else await dialog.dismiss();
   })());
  };
  page.on('dialog',onDialog);
  try{
   // The preceding operation was the old-client GET. No idle wait, synthetic
   // write/receipt, dispatch replacement, or global queue flush intervenes.
   await page.reload();await Promise.all(dialogWork);
   await expect.poll(()=>page.evaluate(()=>!!(window.__settingsReloadEvidence?.firstState&&window.__settingsReloadEvidence?.firstDOM)),
    {message:marker(stage)+' first authoritative WS frame or Settings DOM missing'}).toBe(true);
   record.resumed=await page.evaluate(()=>window.__settingsReloadEvidence);
   await Promise.all([...reading]);
   const first=record.resumed.firstState;
   const attached=evidence.attachments.find(row=>row.sequence>after&&row.clientId===first.clientId);
   record.attach=attached;
   assert.ok(attached,marker(stage)+' no matching attach request');
   assert.equal(attached.resumeClientId,before.clientId,marker(stage)+' wrong resume identity');
   assert.notEqual(attached.clientId,before.clientId,marker(stage)+' reused writable client');
   assert.equal(attached.httpStatus,200,marker(stage)+' attach rejected');
   assert.equal(attached.receipt?.clientId,attached.clientId,marker(stage)+' attach identity mismatch');
   assert.equal(attached.receipt?.hostInstanceId,confirmed.hostInstanceId,marker(stage)+' attach host mismatch');
   assert.equal(record.resumed.resumeClientId,before.clientId,marker(stage)+' storage resume mismatch');
   assert.equal(first.socketClientId,attached.clientId,marker(stage)+' socket client mismatch');
   assert.equal(first.hostInstanceId,confirmed.hostInstanceId,marker(stage)+' resumed host changed');
   assert.ok(first.revision>=confirmed.revision,marker(stage)+' resumed revision regressed');
   assertProviderRoute(first,detail,stage+':first-authoritative-frame');
   assert.deepEqual(first.view,confirmed.view,marker(stage)+':confirmed-navigation-copy');
   const route=detail?'providers/detail':'providers';
   assert.equal(record.resumed.firstDOM.route,route,marker(stage)+':first-DOM-route');
   assert.equal(record.resumed.firstDOM.page,'providers',marker(stage)+':first-DOM-page');
   assert.equal(record.resumed.firstDOM.compact,'true',marker(stage)+':first-DOM-compact');
   assert.deepEqual(first.history,{historyLength:before.historyLength,historyMarker:before.historyMarker},
    marker(stage)+' history changed before first state was handled');
   await expect(root,marker(stage)+':rendered-route').toHaveAttribute('data-settings-route',route);
   await expect.poll(()=>page.evaluate(()=>history.state?.amplifierSettings?.index),
    {message:marker(stage)+' history depth did not reconcile'}).toBe(depth);
   record.after=await presentation();
   assert.equal(record.after.clientId,attached.clientId,marker(stage)+' rendered client mismatch');
   assert.equal(record.after.storedClientId,attached.clientId,marker(stage)+' resume identity not saved');
   assert.equal(record.after.historyLength,before.historyLength,marker(stage)+' duplicated history stack');
   assert.deepEqual(record.after.historyMarker,{token:before.historyMarker.token,index:depth},marker(stage)+' history marker');
   assert.deepEqual(record.dialogs,[{type:'beforeunload',accepted:true}],marker(stage)+' unload decision');
   assert.equal(evidence.dropped,0,marker(stage)+' evidence capped');
  }finally{page.off('dialog',onDialog)}
 }
 return {
  async confirmed({open,select}){
   const record=evidence.confirmed,identity=await presentation();
   stage='confirmed:open';record.listCommand=await command(open,listPatch,identity.clientId);
   await expect(root,marker(stage)).toHaveAttribute('data-settings-route','providers');
   stage='confirmed:select';record.detailCommand=await command(select,detailPatch,identity.clientId);
   await expect(root,marker(stage)).toHaveAttribute('data-settings-route','providers/detail');
   record.before=await presentation();
   assert.equal(record.before.storedClientId,identity.clientId,marker(stage)+' old client not persisted');
   assert.equal(record.before.historyMarker?.index,4,marker(stage)+' detail history depth');
   stage='confirmed:old-client-state';record.oldClient=await readClient(identity.clientId,stage);
   assertConfirmed(record.oldClient,record.listCommand,stage);
   assertConfirmed(record.oldClient,record.detailCommand,stage);
   assertProviderRoute(record.oldClient,true,stage);
   await reload(record,record.before,record.oldClient,true,4);
  },
  async pending({back,select}){
   const record=evidence.pending,identity=await presentation();
   stage='pending:confirmed-list';record.listCommand=await command(back,listPatch,identity.clientId);
   await expect(root,marker(stage)).toHaveAttribute('data-settings-route','providers');
   record.listPresentation=await presentation();
   assert.equal(record.listPresentation.historyMarker?.index,3,marker(stage)+' list history depth');
   record.listState=await readClient(identity.clientId,stage);
   assertConfirmed(record.listState,record.listCommand,stage);
   assertProviderRoute(record.listState,false,stage);
   let abortHeld,primary;
   const abortGate=new Promise(resolve=>{abortHeld=resolve}),held=[],tasks=[];
   const handler=route=>{
    // Do not depend on Playwright's request-event/route-callback ordering to
    // decide whether this write is allowed to reach HTTP.
    const request=route.request();onRequest(request);
    const row=requests.get(request);
    if(row?.clientId!==identity.clientId||row.action!=='view.update'||!detailPatch(row))return route.continue();
    // No fetch/continue/fulfill exists on this branch. Opening the gate ONLY
    // aborts it, even after reload or a failed assertion; it can never land late.
    const entry={commandId:row.commandId,clientId:row.clientId,forwarded:false,abortRequested:false};
    held.push(entry);record.held=held;
    const task=(async()=>{
     await abortGate;entry.abortRequested=true;
     try{await route.abort('aborted');entry.abortOutcome='aborted'}
     catch{entry.abortOutcome='already-cancelled-or-unavailable'}
    })();tasks.push(task);return task;
   };
   await page.route('**/api/actions',handler);
   try{
    stage='pending:held-detail';
    await select();
    await expect(root,marker(stage)).toHaveAttribute('data-settings-route','providers/detail');
    await expect.poll(()=>held.length,{message:marker(stage)+' request not intercepted before HTTP forwarding'}).toBe(1);
    assert.ok(held[0].commandId,marker(stage)+' no real held command ID');
    record.before=await presentation();
    assert.equal(record.before.historyMarker?.index,4,marker(stage)+' optimistic detail history depth');
    record.oldClient=await readClient(identity.clientId,stage);
    assertConfirmed(record.oldClient,record.listCommand,stage);
    assertProviderRoute(record.oldClient,false,stage+':last-confirmed-list');
    await reload(record,record.before,record.oldClient,false,3);
   }catch(error){primary=error;throw error}
   finally{
    abortHeld();let cleanupFailed=false;
    for(const [operation,perform] of [
     ['abort-held-writes',async()=>{await Promise.all(tasks);if(held.some(entry=>entry.abortOutcome!=='aborted'))throw Error('Abort outcome unavailable')}],
     ['remove-route-handler',()=>page.unroute('**/api/actions',handler)],
    ]){
     try{await boundedRead(perform());evidence.cleanup.push({stage,operation,status:'completed'})}
     catch{cleanupFailed=true;evidence.cleanup.push({stage,operation,status:'unavailable',primaryPreserved:!!primary})}
    }
    if(cleanupFailed&&!primary)throw Error(marker(stage)+' pending write cleanup unavailable');
   }
   stage='pending:retired';
   record.oldClientAfter=await readClient(identity.clientId,stage);
   assertProviderRoute(record.oldClientAfter,false,stage+':old-client-unchanged');
   for(const entry of held){
    assert.equal(entry.forwarded,false,marker(stage)+' held write forwarded');
    assert.equal(evidence.commands.find(row=>row.commandId===entry.commandId)?.receipt,undefined,marker(stage)+' held write has a receipt');
   }
   stage='reload-checks-complete';
  },
  async save(error){
   if(error)evidence.failure={stage,name:error.name}; // Error text can contain private assertion values.
   try{evidence.lastPresentation=await presentation()}catch{evidence.lastPresentationUnavailable=true}
   await Promise.all([...reading]);
   await writeFile(join(directory,'settings-mobile-reload-evidence.json'),JSON.stringify(evidence,null,2)+'\n');
  },
  dispose(){
   if(disposed)return;disposed=true;
   page.off('request',onRequest);page.off('response',onResponse);page.off('requestfailed',onFailed);
  },
 };
}