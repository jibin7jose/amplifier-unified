// Private helper for destination-history-browser.mjs, not a standalone fixture.
// Delay real catalog commands BEFORE host admission: holding only HTTP replies
// would still allow websocket publications to hydrate the mounted editor early.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {expect} from '@playwright/test';

export const privateWorkspaceText='{"unsaved-workspace-navigation":';
const token=value=>typeof value==='string'&&/^[\w.:-]{1,160}$/.test(value)?value:null;
const number=value=>Number.isFinite(value)?value:null;
const target=value=>({kind:token(value?.kind),workspaceId:token(value?.workspaceId)});
const bounded=async(promise,ms=2000)=>{
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Provider diagnostic deadline')),ms)})])}
 finally{clearTimeout(timer)}
};

export function observeSettingsProviderPreservation(page,{workspaceId,clientId,hostInstanceId,gate}){
 const started=performance.now(),events=[],held=[],requests=new WeakMap(),receipts=new Map(),pending=new Set(),catalogRequests=new Set();
 let dropped=0,bodyReads=0,heldCount=0,released=false,stopped=false,saved=false,oracle=null,privateTextSent=false;
 const record=(kind,data={})=>{if(stopped)return;if(events.length<96)events.push({atMs:performance.now()-started,kind,...data});else dropped++};
 const requested=request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const command=request.postDataJSON();
  const row={id:token(command.id),action:token(command.action),clientId:token(request.headers()['x-amplifier-client']),
   configurationTarget:target(command.args?.configurationTarget),privateTextSent:JSON.stringify(command).includes('unsaved-workspace-navigation')};
  privateTextSent||=row.privateTextSent;
  if(row.action==='providers.list'&&row.clientId===clientId&&row.configurationTarget.kind==='workspace'&&row.configurationTarget.workspaceId===workspaceId)catalogRequests.add(row.id);
  requests.set(request,row);record('request',row);
 };
 const responded=response=>{
  const row=requests.get(response.request());if(!row||stopped)return;
  // Reserve receipt capacity for the held commands; incidental editor traffic
  // cannot consume the evidence needed to join this gate to its publication.
  const required=catalogRequests.has(row.id);
  if((!required&&(bodyReads>=24||pending.size>=4))||Number(response.headers()['content-length'])>524288){record('receipt-not-read',{id:row.id});return}
  bodyReads++;
  const task=bounded(response.body()).then(bytes=>{
   if(stopped)return;
   if(bytes.length>524288){record('receipt-not-read',{id:row.id});return}
   const value=JSON.parse(bytes.toString()),receipt={id:row.id,status:response.status(),accepted:typeof value.accepted==='boolean'?value.accepted:null,
    revision:number(value.revision),stateRevision:number(value.stateRevision),hostInstanceId:token(value.hostInstanceId),hasEmbeddedState:!!value.state};
   receipts.set(row.id,receipt);record('receipt',receipt);
  }).catch(()=>record('receipt-unavailable',{id:row.id})).finally(()=>pending.delete(task));
  pending.add(task);
 };
 const intercept=async route=>{
  const request=route.request(),command=request.postDataJSON(),args=command?.args;
  if(command?.action==='providers.list'&&request.headers()['x-amplifier-client']===clientId&&
   args?.configurationTarget?.kind==='workspace'&&args.configurationTarget.workspaceId===workspaceId&&!released){
   heldCount++;
   if(held.length<8)held.push({id:token(command.id),configurationTarget:target(args.configurationTarget)});
   record('providers-list-held',{id:token(command.id),configurationTarget:target(args.configurationTarget)});
   await gate.wait;
  }
  // Teardown retires a held read rather than allowing it to land after failure.
  if(stopped){try{await route.abort()}catch{};return}
  await route.continue();
 };
 const mark=label=>bounded(page.evaluate(label=>window.__settingsProviderPreservation.mark(label),label));
 return {
  async start(){
   page.on('request',requested);page.on('response',responded);
   await page.route('**/api/actions',intercept);
   await bounded(page.evaluate(({workspaceId,privateText})=>{
    const started=performance.now(),records=[],checkpoints={},ids=new WeakMap();
    const pinned=new Set(['before-workspace-mount','private-editor-bound','before-first-back','prompt-0','after-keep-0','after-first-keep-before-list-release','after-list-publication']);
    let nextId=0,dropped=0,observationErrors=0,last='',baseline=null,firstReplacement=null,restoreSetter=()=>{};
    const token=value=>typeof value==='string'&&/^[\w.:-]{1,160}$/.test(value)?value:null;
    const identity=node=>{if(!node)return null;if(!ids.has(node))ids.set(node,++nextId);return ids.get(node)};
    const region=()=>document.querySelector('[aria-label="Workspace configuration"]');
    const editor=()=>region()?.querySelector('[id$="provider-config"]');
    const section=()=>region()?.querySelector('[data-part="provider-settings"]');
    const snapshot=()=>{
     const s=window.amplifier.getState(),setup=s.setup||{},view=s.view||{},node=editor(),root=section();
     const draft=view.configurationDrafts?.['workspace:'+workspaceId]?.providerEditor;
     const scoped=setup.configurationTarget?.kind==='workspace'&&setup.configurationTarget.workspaceId===workspaceId;
     const op=Object.entries(setup.operations||{}).find(([key,op])=>key.split('\n').at(-1)==='providers.list:'&&
      op.configurationTarget?.kind==='workspace'&&op.configurationTarget.workspaceId===workspaceId)?.[1];
     const historyText=sessionStorage.getItem('amplifier.destinationHistory'),trail=JSON.parse(historyText||'null');
     const fixture=scoped&&setup.providers?.find(row=>row.id==='test-provider'&&row.enabled!==false);
     return {atMs:performance.now()-started,revision:s.revision,clientId:token(s.client?.id),hostInstanceId:token(s.client?.hostInstanceId),
      selectedSessionId:token(s.selectedSessionId),selectedWorkspaceId:token(s.selectedWorkspaceId),
      route:{workSurface:token(view.workSurface),workspaceId:token(view.workWorkspaceId),tab:token(view.workWorkspaceTab),settings:token(view.workWorkspaceSettings)},
      draft:{dirty:!!draft?.dirty,dirtyPresent:Object.hasOwn(draft||{},'dirty'),id:draft?.id===''?'':token(draft?.id),
       selectionKey:draft?.selectionKey===''?'':token(draft?.selectionKey),privateEqualsShared:!!node&&node.value===draft?.config,
       sharedContainsPrivate:typeof draft?.config==='string'&&draft.config.includes('unsaved-workspace-navigation')},
      privateEqualsExpected:!!node&&node.value===privateText,editorId:identity(node),providerSectionId:identity(root),
      sameEditor:baseline?node===baseline.node:null,sameProviderSection:baseline?root===baseline.root:null,
      originalEditorConnected:baseline?baseline.node.isConnected:null,
      prompt:!!document.querySelector('[role="alertdialog"]'),navigationPending:!!s.navigationPending,
      history:{cursor:trail?.cursor,entries:trail?.entries.length,forward:!!trail&&trail.cursor<trail.entries.length-1,
       unchanged:baseline?historyText===baseline.historyText:null},
      providers:{target:{kind:token(setup.configurationTarget?.kind),workspaceId:token(setup.configurationTarget?.workspaceId)},
       loadedAt:scoped?setup.providersLoadedAt??null:null,hasFixture:!!fixture,fixtureModelIsFirst:fixture?.config?.model==='first',
       replacementEqualsSaved:!!node&&!!fixture&&node.value===JSON.stringify(fixture.config,null,2),
       commandId:token(op?.commandId),phase:token(op?.phase),loading:root?.querySelector('[aria-label="Refresh providers"]')?.getAttribute('aria-busy')==='true'}};
    };
    const capture=(label,force=false)=>{
     try{
      const value=snapshot(),key=JSON.stringify({...value,atMs:0});
      if(baseline&&!value.privateEqualsExpected&&!firstReplacement)firstReplacement={label,...value};
      if(pinned.has(label))checkpoints[label]=value;
      if(force||key!==last){last=key;if(records.length<96)records.push({label,...value});else dropped++}
      return value;
     }catch{
      // Observation must not throw from the application's native value setter.
      observationErrors++;
      if(records.length<96)records.push({label,unavailable:true});else dropped++;
      return {unavailable:true};
     }
    };
    const unsubscribe=window.amplifier.subscribe(()=>capture('state-publication'));
    const observer=new MutationObserver(()=>capture('DOM-mutation'));
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-busy','aria-selected']});
    window.__settingsProviderPreservation={
     mark:label=>capture(label,true),
     bind(){
      const node=editor(),root=section();if(!node||!root||node.value!==privateText)throw Error('Private editor not established');
      baseline={node,root,historyText:sessionStorage.getItem('amplifier.destinationHistory')};
      // Observe the actual application assignment synchronously, including the
      // first replacement. Delegate the original setter exactly once; never
      // invent a value, dispatch input, or alter React/application state.
      const own=Object.getOwnPropertyDescriptor(node,'value'),descriptor=own||Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');
      Object.defineProperty(node,'value',{configurable:true,enumerable:descriptor.enumerable,get:descriptor.get,
       set(value){descriptor.set.call(this,value);capture('textarea-value-set',true)}});
      restoreSetter=()=>{if(own)Object.defineProperty(node,'value',own);else delete node.value};
      return capture('private-editor-bound',true);
     },
     read:()=>({current:snapshot(),firstReplacement,observationErrors}),
     finish(label){const final=capture(label,true);observer.disconnect();unsubscribe();restoreSetter();
      delete window.__settingsProviderPreservation;return {records,checkpoints,dropped,observationErrors,firstReplacement,final}}
    };
    capture('installed',true);
   },{workspaceId,privateText:privateWorkspaceText}));
   const initial=await mark('before-workspace-mount');
   assert.equal(initial.providers.loadedAt,null,'providers-list-gate requires a not-yet-loaded workspace target');
  },
  async waitForHeld(count){
   await expect.poll(()=>heldCount,{timeout:10000,message:'Real workspace providers.list must reach the command gate'}).toBeGreaterThanOrEqual(count);
   return heldCount;
  },
  async bind(){
   const value=await bounded(page.evaluate(()=>window.__settingsProviderPreservation.bind()));
   assert.equal(value.draft.dirty,false,'Invalid JSON must exercise private-only dirtiness');
   assert.equal(value.draft.privateEqualsShared,false);assert.equal(value.draft.sharedContainsPrivate,false);
   assert.equal(value.providers.loadedAt,null,'No workspace provider publication before private text');
  },
  mark,
  async releaseAndVerify(){
   const before=await mark('after-first-keep-before-list-release');
   assert.equal(before.privateEqualsExpected,true);assert.equal(before.prompt,false);
   assert.equal(before.draft.dirty,false);assert.equal(before.providers.loadedAt,null);
   assert.equal(before.sameEditor,true);assert.equal(before.sameProviderSection,true);
   assert.ok(held.length>0&&heldCount===held.length,'Bounded gate must retain every held command');
   assert.equal(new Set(held.map(row=>row.id)).size,heldCount,'Each mount read must have its own command ID');
   released=true;record('providers-list-released',{commandIds:held.map(row=>row.id)});gate.release();
   await expect.poll(()=>held.every(row=>receipts.has(row.id)),{timeout:10000,message:'Real providers.list receipts observed'}).toBe(true);
   for(const {id} of held){const receipt=receipts.get(id);assert.ok(id);assert.equal(receipt.status,200);
    assert.equal(receipt.accepted,true);assert.equal(receipt.hostInstanceId,hostInstanceId);assert.ok(Number.isSafeInteger(receipt.stateRevision))}
   await expect.poll(async()=>{
    const value=await mark('awaiting-list-publication'),receipt=receipts.get(value.providers.commandId);
    return value.providers.phase==='ready'&&catalogRequests.has(value.providers.commandId)&&receipt?.accepted===true&&
     value.providers.loadedAt>0&&value.providers.hasFixture&&value.providers.fixtureModelIsFirst&&
     value.clientId===clientId&&value.hostInstanceId===hostInstanceId&&value.revision>=receipt?.stateRevision&&!value.providers.loading;
   },{timeout:10000,message:'Real workspace providers.list publication and rendered completion'}).toBe(true);
   // The ready catalog plus cleared local pending UI is the render boundary;
   // two animation frames allow the passive hydration/configText effects to commit.
   await bounded(page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))));
   await mark('after-list-publication');
   const result=await bounded(page.evaluate(()=>window.__settingsProviderPreservation.read()));
   oracle={marker:'private-workspace-text-preserved',passed:result.current.privateEqualsExpected&&!result.firstReplacement};
   assert.equal(result.observationErrors,0,'Provider preservation observation must remain available');
   assert.equal(result.current.sameEditor,true,'Provider textarea must not remount');
   assert.equal(result.current.sameProviderSection,true,'Provider settings must not remount');
   assert.equal(result.current.history.unchanged,true);assert.deepEqual(result.current.route,before.route);
   assert.equal(result.current.selectedSessionId,before.selectedSessionId);
   assert.equal(result.current.selectedWorkspaceId,before.selectedWorkspaceId);
   assert.equal(privateTextSent,false,'Private workspace text must not reach the host');
   // Immediate boolean assertion, not a toHaveValue timeout that could hide the
   // first replacement or print the contents of a private configuration field.
   assert.equal(oracle.passed,true,oracle.marker);
  },
  async save(label,outputPath){
   if(saved)return;
   let observation={unavailable:true};
   try{observation=await bounded(page.evaluate(label=>window.__settingsProviderPreservation?.finish(label),label))||observation}
   catch{record('final-observation-unavailable')}
   try{await bounded(Promise.all([...pending]))}catch{record('pending-receipts-unavailable')}
   stopped=true;gate.release();page.off('request',requested);page.off('response',responded);
   await writeFile(outputPath,JSON.stringify({schema:1,label,oracle,held,heldCount,released,receipts:[...receipts.values()],events,dropped,privateTextSent,observation,
    limits:{maxEvents:96,maxSnapshots:96,pinnedCheckpoints:7,maxHeldCommands:8,incidentalReceiptReads:24,concurrentIncidentalReceiptReads:4,reservedReceiptReads:8,
     receiptBytes:524288,receiptAcquisition:'Content-Length when present; actual size checked after acquisition',
     diagnosticReadDeadlineMs:2000,privateBodiesRetained:false,credentialsRetained:false,syntheticState:false,
     clock:'Separate Node/browser performance.now clocks; command IDs join receipt and publication'}},null,2));
   saved=true;await bounded(page.unroute('**/api/actions',intercept));
  }
 };
}
