// Ordinary native preparation + real rendered controls; only providers are synthetic.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile,appendFile,realpath,readdir} from 'node:fs/promises';
import {isAbsolute,relative,resolve} from 'node:path';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showComposerOption} from './composer-options-helpers.mjs';
import {discoveryProblems,listRequestProblems} from './provider-native-readiness.mjs';

const root=process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(root,'An owned artifact directory is required');
assert.ok(isAbsolute(root),'Artifact root must be absolute, independently of Node cwd');
const evidence=root+'/provider1201-native';
await mkdir(root,{recursive:true});
await mkdir(evidence); // Never overwrite an earlier attempt's partial records.
const save=(name,value)=>writeFile(`${evidence}/${name}`,JSON.stringify(value,null,2)+'\n');
async function bounded(promise,ms,label){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),ms)})])}
 finally{clearTimeout(timer)}
}
const installed=process.env.AMPLIFIER_TEST_INSTALLED==='1';
const checkout=await realpath(fileURLToPath(new URL('../..',import.meta.url)));
const under=(base,path)=>{const part=relative(base,path);return part===''||!part.startsWith('..')&&!isAbsolute(part)};
assert.ok(process.env.AMPLIFIER_TEST_PYTHON,'Prepared interpreter must be supplied; no fallback');
if(installed){
 assert.ok(process.env.PROVIDER_NATIVE_FIXTURE,'Installed mode requires an externally copied fixture');
 assert.ok(process.env.AMPLIFIER_TEST_INSTALLED_IDENTITY,'Installed mode requires the exact wheel files manifest');
 assert.ok(!under(checkout,await realpath(evidence)),'Installed fixture cwd/artifacts must be outside the checkout');
}
const fixturePath=await realpath(process.env.PROVIDER_NATIVE_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/provider_native_ui_server.py',import.meta.url)));
if(installed)assert.ok(!under(checkout,fixturePath),'Installed fixture must be outside the checkout');
const fixtureEnv={...process.env,PYTHONNOUSERSITE:'1',PYTHONDONTWRITEBYTECODE:'1'};
delete fixtureEnv.PYTHONPATH;
await save('launch.json',{mode:installed?'installed':'source',fixturePath,python:process.env.AMPLIFIER_TEST_PYTHON,
 cwd:evidence,pythonPathUnset:true,fixtureSha256:createHash('sha256').update(await readFile(fixturePath)).digest('hex'),
 installedIdentity:process.env.AMPLIFIER_TEST_INSTALLED_IDENTITY||null});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON,[fixturePath,evidence+'/fixture'],
 {stdio:['ignore','pipe','pipe'],cwd:evidence,env:fixtureEnv});
const exit=new Promise(resolve=>fixture.once('exit',(code,signal)=>resolve({code,signal})));
const drained=new Promise(resolve=>fixture.once('close',resolve));
let fixtureOutput='',fixtureErrors='',browser,page,primary,proof,flush,journey,discoveryRead;
const cleanupErrors=[];
let logWrites=Promise.resolve();
fixture.stdout.on('data',chunk=>{fixtureOutput+=chunk;logWrites=logWrites.then(()=>appendFile(evidence+'/fixture-stdout.log',chunk))});
fixture.stderr.on('data',chunk=>{fixtureErrors+=chunk;logWrites=logWrites.then(()=>appendFile(evidence+'/fixture-stderr.log',chunk))});
const pageErrors=[],evidenceErrors=[],actions=[],responses=[],assets=[],boundaries=[];
try{
 const url=await bounded(new Promise((resolve,reject)=>{
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited before readiness: '+code+'\n'+fixtureErrors)));
  fixture.stdout.on('data',()=>{for(const line of fixtureOutput.split('\n'))if(/^http:\/\/127\.0\.0\.1:\d+$/.test(line.trim()))resolve(line.trim())});
 }),60000,'Native fixture readiness');
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 // Observe configuration synchronously at the actual fetch call. This is
 // browser-observed state, not a fabricated server configuration revision.
 // Forward exactly once with unchanged arguments; no delays or extra actions.
 await context.addInitScript(()=>{
  const original=window.fetch,observations=[];
  window.__providerListObservations=observations;
  window.fetch=function(input,init){
   try{
    if(new URL(typeof input==='string'?input:input.url,location.href).pathname==='/api/actions'&&init?.method==='POST'){
     const command=JSON.parse(init.body);
     if(command.action==='providers.list'){
      const state=window.amplifier?.getState();
      observations.push({command,sequence:observations.length,clientId:new Headers(init.headers).get('X-Amplifier-Client'),
       observedClientId:state?.client?.id,hostInstanceId:state?.client?.hostInstanceId,configurationRevision:state?.configurationRevision??0,
       revision:state?.revision});
     }
    }
   }catch(error){observations.push({error:error.message})}
   return original.apply(this,arguments);
  };
 });
 page=await context.newPage();page.setDefaultTimeout(10000);
 page.on('pageerror',error=>pageErrors.push(error.message));
 const requests=new Map();let eventWrites=Promise.resolve();
 function record(row){eventWrites=eventWrites.then(()=>appendFile(evidence+'/actions.jsonl',JSON.stringify(row)+'\n')).catch(error=>evidenceErrors.push(error.message))}
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const row={sequence:actions.length,command:request.postDataJSON(),clientId:request.headers()['x-amplifier-client'],phase:'sent'};
  requests.set(request,row);actions.push(row);record({...row});
 });
 page.on('response',response=>{
  const row=requests.get(response.request());if(!row)return;
  responses.push(bounded(response.json(),10000,'Action '+row.command.id).then(receipt=>{
   Object.assign(row,{phase:'received',status:response.status(),receipt});record({...row});
  }).catch(error=>{Object.assign(row,{phase:'receipt-unavailable',error:error.message});record({...row})}));
 });
 page.on('requestfailed',request=>{
  const row=requests.get(request);if(row){Object.assign(row,{phase:'transport-failed',error:request.failure()});record({...row})}
 });
 async function sourceStatic(folder,prefix='amplifier_web/static'){
  const manifest={};
  for(const row of await readdir(folder,{withFileTypes:true})){
   assert.ok(!row.isSymbolicLink(),'Static links are not accepted');
   if(row.isDirectory())Object.assign(manifest,await sourceStatic(`${folder}/${row.name}`,`${prefix}/${row.name}`));
   else if(row.isFile())manifest[`${prefix}/${row.name}`]=createHash('sha256').update(await readFile(`${folder}/${row.name}`)).digest('hex');
  }
  return manifest;
 }
 if(installed)assert.ok(process.env.PROVIDER_SELECTION_STATIC_MANIFEST,'Installed checks require an independently bound static manifest');
 const staticManifest=process.env.PROVIDER_SELECTION_STATIC_MANIFEST?
  JSON.parse(await readFile(process.env.PROVIDER_SELECTION_STATIC_MANIFEST,'utf8')):
  await sourceStatic(checkout+'/amplifier_web/static');
 await save('expected-static.json',staticManifest);
 const assetPromises=[];let assetWrites=Promise.resolve();
 page.on('response',response=>{
  const address=new URL(response.url());if(address.origin!==new URL(url).origin)return;
  const path=address.pathname==='/'?'index.html':address.pathname.slice(1),expected=staticManifest['amplifier_web/static/'+path];
  if(!expected&&!path.startsWith('assets/')&&(path.startsWith('api/')||!['script','stylesheet','image','font','media'].includes(response.request().resourceType())))return;
  assetPromises.push((async()=>{
   assert.ok(expected,'Unmanifested static response '+path);assert.equal(response.status(),200);
   const sha256=createHash('sha256').update(await bounded(response.body(),10000,'Static '+path)).digest('hex');
   assert.equal(sha256,expected,path);return{path,sha256};
  })().catch(error=>({path,error:error.message})).then(row=>{
   assets.push(row);assetWrites=assetWrites.then(()=>appendFile(evidence+'/consumed-assets.jsonl',JSON.stringify(row)+'\n'));return row;
  }));
 });
 flush=async()=>{
  const pending=[...actions];
  await expect.poll(()=>pending.filter(row=>row.phase==='sent').length,{timeout:12000}).toBe(0);
  await bounded(Promise.all(responses),12000,'Observed action receipts');await eventWrites;
  await bounded(Promise.all(assetPromises),12000,'Consumed assets');await assetWrites;
  await save('actions.json',actions);await save('consumed-assets.json',assets);
 };
 proof=async()=>{
  const response=await page.request.get(url+'/fixture/native-proof',{timeout:10000});assert.equal(response.status(),200);return response.json();
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const dialog=page.getByRole('dialog',{name:'Chat options',exact:true});
 const A={instance:'native-a',model:'native-model',effort:'low'};
 const B={instance:'native-b',model:'native-model',effort:'high'};
 const ALT={instance:'native-alt',model:'native-model',effort:'high'};
 const connectionLabels={'native-a':'Native A','native-b':'Native B','native-alt':'Native alternate'};
 const label=instance=>{assert.ok(connectionLabels[instance],'Unknown connection label');return `${connectionLabels[instance]} (${instance})`};
 const managedKey=JSON.stringify(['','','managed']);
 const managedWorkspace=evidence+'/fixture/app';
 const discoverySnapshots=[];
 // Read the exact browser client's saved state, not its optimistic pending overlay.
 discoveryRead=async()=>{
  const client=(await state()).client;
  const response=await page.request.get(url+'/api/state?'+new URLSearchParams({clientId:client.id}),{timeout:10000});
  assert.equal(response.status(),200);
  const value=await response.json();assert.equal(value.client.id,client.id);
  assert.equal(value.client.hostInstanceId,client.hostInstanceId);
  const canonical=(await proof()).discovery;
  assert.equal(canonical.hostInstanceId,client.hostInstanceId);
  const row={client:value.client,revision:value.revision,configurationRevision:value.configurationRevision||0,
   selectedSessionId:value.selectedSessionId,view:{panel:value.view?.panel,setupJourney:value.view?.setupJourney,newSessionDraft:value.view?.newSessionDraft},
   setup:{...Object.fromEntries(['providers','providerCatalogs','providersWorkspace','providersRequestedWorkspace','providersLocation','providersLoadedAt'].map(key=>[key,value.setup?.[key]]))},
   defaults:value.draftDefaults?.[managedKey]||null,defaultsKey:managedKey,canonical};
  discoverySnapshots.push(row);await appendFile(evidence+'/discovery-state.jsonl',JSON.stringify(row)+'\n');
  return row;
 };
 async function completionFor(id,name,args,receipt,client){
  assert.equal(receipt.accepted,true);
  let completion;
  await expect.poll(async()=>{
   const response=await page.request.get(url+'/api/state/detail?'+new URLSearchParams({clientId:client.id,path:'/managementResults/'+id}),{timeout:10000});
   const body=await response.json();
   completion={id,name,args,receipt,client,status:response.status(),body};
   await appendFile(evidence+'/discovery-completions.jsonl',JSON.stringify(completion)+'\n');
   if(response.status()===400&&body.accepted===false&&body.error==='Invalid request: That state path does not exist.')return 'pending';
   assert.equal(response.status(),200);
   return Object.fromEntries((body.items||[]).map(row=>[row.key,row.value])).phase;
  },{timeout:10000}).toMatch(/^(ready|error)$/);
  const result=Object.fromEntries(completion.body.items.map(row=>[row.key,row.value]));
  assert.equal(result.phase,'ready',name+' completion');assert.equal(result.error,null);
  return completion;
 }
 async function completedRead(name,args){
  const id=crypto.randomUUID(),receipt=await page.evaluate(([name,args,id])=>window.amplifier.dispatch(name,args,{id}),[name,args,id]);
  return completionFor(id,name,args,receipt,(await state()).client);
 }
 let exerciseOrderedSuccessor=true;
 async function settleManagedDraft(){
  const before=await discoveryRead();
  assert.equal(before.view.setupJourney,'done','Visible first-connection flow must have completed');
  const listArgs={workspace:'',location:{kind:'managed'}};
  let completed,orderedSuccessor;
  if(exerciseOrderedSuccessor){
   // Deterministically exercise the observed ordering using actual accepted
   // actions/completions, not fabricated setup rows or selected providers.
   const original=await completedRead('providers.list',listArgs);
   orderedSuccessor=await completedRead('providers.list',listArgs);
   completed=[original,await completedRead('configuration.defaults',{workspace:'',bundle:'',location:{kind:'managed'}})];
   assert.notEqual(original.id,orderedSuccessor.id);
   await save('ordered-list-successor.json',{original,successor:orderedSuccessor,defaults:completed[1]});
   exerciseOrderedSuccessor=false;
  }else completed=await Promise.all([
   completedRead('providers.list',listArgs),
   completedRead('configuration.defaults',{workspace:'',bundle:'',location:{kind:'managed'}}),
  ]);
  const binding={clientId:before.client.id,hostInstanceId:before.client.hostInstanceId,configurationRevision:before.configurationRevision,
   minimumRevision:Math.max(before.revision,...completed.map(row=>row.body.revision)),listId:completed[0].id,
   listArgs:{workspace:'',location:{kind:'managed'}},managedKey,managedWorkspace,rootBundle:evidence+'/fixture/root.yaml',effective:A};
  const completions={[completed[0].id]:completed[0]};
  if(orderedSuccessor)completions[orderedSuccessor.id]=orderedSuccessor;
  let ready,joined;
  await expect.poll(async()=>{
   const selected=await discoveryRead(),candidateId=selected.canonical?.setup?.operations?.['providers.list:']?.commandId;
   const observations=await page.evaluate(()=>window.__providerListObservations);
   await save('list-request-observations.json',observations);
   const observed=actions.map(row=>{
    const matches=observations.filter(value=>value.command?.id===row.command.id);
    return {...row,witness:matches.length===1?matches[0]:null};
   });
   const candidates=observed.filter(row=>row.command.id===candidateId),originals=observed.filter(row=>row.command.id===binding.listId);
   if(candidates.length===1&&originals.length===1&&!completions[candidateId]&&
      !listRequestProblems(originals[0],binding).length&&!listRequestProblems(candidates[0],binding,originals[0]).length){
    const candidate=candidates[0];
    completions[candidateId]=await completionFor(candidateId,candidate.command.action,candidate.command.args,candidate.receipt,before.client);
   }
   // Completion evidence is per-command; the slot can change during its lookup.
   // Re-read instead of declaring a historical completion to be current.
   ready=await discoveryRead();joined={actions:observed,completions,candidateId};
   const problems=discoveryProblems(ready,binding,joined);
   await appendFile(evidence+'/list-currentness.jsonl',JSON.stringify({binding,selected,ready,candidateId,problems,
    original:originals[0],candidate:candidates[0],completions})+'\n');
   return problems;
  },{timeout:10000}).toEqual([]);
  if(orderedSuccessor)assert.notEqual(joined.candidateId,binding.listId,'Successful later list must own the current slot');
  await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeHidden();
  assert.equal(ready.view.setupJourney,'done');
  const checks=[];
  for(const [name,mutate,reason] of [
   ['old-snapshot',row=>{row.revision=binding.minimumRevision-1},'stale revision'],
   ['wrong-scope',row=>{row.setup.providersLocation={kind:'workspace'}},'catalog scope'],
   ['empty-provider-snapshot',row=>{row.setup.providers=[];row.setup.providerCatalogs={}},'provider rows'],
   ['wrong-operation',row=>{row.canonical.setup.operations['providers.list:'].commandId='old-operation'},'list currentness'],
   ['old-configuration',row=>{row.configurationRevision--},'stale revision'],
  ]){
   const bad=structuredClone(ready);mutate(bad);const rejected=discoveryProblems(bad,binding,joined);
   assert.ok(rejected.includes(reason),name);checks.push({name,rejected});
  }
  await save('discovery-readiness.json',{binding,completed,ready,listEvidence:joined,negativeControls:checks});
  return ready;
 }
 async function close(){
  const current=await state(),baseline=actions.length;
  const matches=request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
   const command=request.postDataJSON(),patch=command.args?.patch?.composerModel;
   return command.action==='view.update'&&patch?.open===false&&
    (patch.sessionId??null)===(current.selectedSessionId??null)&&request.headers()['x-amplifier-client']===current.client.id;
  };
  const receiptPromise=page.waitForResponse(response=>matches(response.request()),{timeout:15000});
  await dialog.getByRole('button',{name:'Close chat options',exact:true}).click();
  await expect(dialog).toBeHidden();await expect(model).toHaveAttribute('aria-expanded','false');
  const response=await receiptPromise,request=response.request();
  const row=requests.get(request);assert.ok(row&&row.sequence>=baseline);
  const receipt=await bounded(response.json(),10000,'Exact close receipt');
  assert.equal(response.status(),200);assert.equal(receipt.accepted,true);
  await appendFile(evidence+'/close-receipts.jsonl',JSON.stringify({command:request.postDataJSON(),receipt,clientId:current.client.id})+'\n');
 }
 async function visible(expected,timeout=10000){
  const row={expected};boundaries.push(row);await save('visible.json',boundaries);
  if(!await dialog.isVisible())await model.click();
  await showComposerOption(page,'provider');const provider=dialog.locator('#chat-provider');
  await expect(provider).toBeVisible();row.instance=await provider.inputValue();row.label=await provider.locator('option:checked').innerText();await save('visible.json',boundaries);
  await expect(provider,'Exact visible connection').toHaveValue(expected.instance,{timeout});
  await expect(provider.locator('option:checked')).toHaveText(label(expected.instance),{timeout});
  row.instance=await provider.inputValue();row.label=await provider.locator('option:checked').innerText();
  await showComposerOption(page,'model');const selectedModel=dialog.locator('#chat-model');
  await expect(selectedModel).toBeVisible();await expect(selectedModel).toHaveValue(expected.model,{timeout});
  row.model=await selectedModel.inputValue();
  await showComposerOption(page,'effort');const effort=dialog.locator('#chat-effort');
  await expect(effort).toBeVisible();await expect(effort).toHaveAttribute('aria-valuetext',expected.effort,{timeout});
  row.effort=await effort.getAttribute('aria-valuetext');await close();
  await expect(model.locator(':scope > span')).toHaveText(`${expected.model} (${expected.effort})`,{timeout});
  row.chip=await model.locator(':scope > span').innerText();row.dismissed=true;
  await save('visible.json',boundaries);return row;
 }
 const tuple=value=>({instance:value.instance||value.id,model:value.model,effort:value.effort});
 let sendIndex=0,firstNative=null,acceptedConfiguration=null;
 async function send(expected,existingId=null){
  const index=sendIndex++,text='Native provider boundary '+index;
  if(!existingId){
   // A manual draft title suppresses auxiliary Auto naming without pinning a model.
   const current=await state();
   const receipt=await action('view.update',{patch:{newSessionDraft:{...current.view.newSessionDraft,title:'Native boundary chat '+index}}});
   assert.equal(receipt.accepted,true);
  }
  await composer.fill(text);
  const before={index,text,display:await visible(expected),proof:await proof()};
  assert.equal(before.proof.calls.length,index);await expect(composer).toHaveText(text);
  await save(`send-${index}-before.json`,before);
  const sentPromise=page.waitForResponse(response=>{
   const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
   const command=request.postDataJSON();return command.action==='conversation.send'&&command.args?.text===text;
  },{timeout:60000});
  await composer.press('Enter');
  const response=await sentPromise;const command=response.request().postDataJSON(),receipt=await bounded(response.json(),10000,'Send receipt');
  await save(`send-${index}-receipt.json`,{command,status:response.status(),receipt});
  assert.equal(response.status(),200);assert.equal(receipt.accepted,true);assert.equal(receipt.delivery,'accepted');
  assert.equal(receipt.sessionId,command.args.sessionId);assert.ok(typeof receipt.inputId==='string'&&receipt.inputId);
  await expect.poll(async()=>{const current=await proof();await save('native-current.json',current);return current.calls.length},{timeout:90000}).toBe(index+1);
  let after;
  await expect.poll(async()=>{
   after=await proof();
   const terminal=after.lifecycle.find(row=>row.kind==='runtime.generation'&&row.event==='generation.finished'&&
    row.sessionId===receipt.sessionId&&(row.input_ids||[]).includes(receipt.inputId));
   return !!terminal&&after.sessions.find(row=>row.id===receipt.sessionId)?.status==='idle';
  },{timeout:30000}).toBe(true);
  await save(`send-${index}-after.json`,after);
  const call=after.calls[index];assert.deepEqual(tuple(call),expected);
  assert.deepEqual(call.providerDefaults,{model:expected.model,reasoning_effort:expected.effort},
   'Low/high are provider defaults, not evidence of an explicit xhigh pin');
  const finished=after.lifecycle.find(row=>row.kind==='runtime.generation'&&row.event==='generation.finished'&&
   row.sessionId===receipt.sessionId&&(row.input_ids||[]).includes(receipt.inputId));
  const started=after.lifecycle.find(row=>row.kind==='runtime.generation'&&row.event==='generation.started'&&
   row.sessionId===receipt.sessionId&&row.generation_id===finished?.generation_id&&row.initial_input_id===receipt.inputId);
  assert.ok(started&&finished&&finished.generation_id,'Join admitted input to one actual native generation');
  // Server reduction time is not worker emission time: transport can deliver a
  // started event after the provider entered. Join IDs, not that timing accident.
  assert.ok(started.atNs<=finished.atNs&&call.atNs<=finished.atNs);
  const session=after.sessions.find(row=>row.id===receipt.sessionId);assert.ok(session);
  assert.equal(session.controlsPresent,true);
  assert.equal(call.sessionId,session.nativeId);assert.ok(call.inputIds.includes(receipt.inputId),'Actual provider input identity must match admitted send');
  if(existingId)assert.equal(session.id,existingId);
  const worker=after.workers.find(row=>row.kind==='runtime_worker'&&row.pid===call.pid);assert.ok(worker);
  const ready=[...after.workerReady].reverse().find(row=>row.sessionId===session.id&&row.pid===call.pid&&row.atNs<call.atNs);assert.ok(ready);
  assert.equal(ready.report.session_id,session.nativeId);
  assert.equal(session.mountedReport.session_id,session.nativeId);
  assert.equal(session.mountedReport.contextIntelligence.enabled,true);
  // Existing-worker B may be applied through provider.select after its original A preparation.
  // Preserve that actual distinction instead of relabeling the initial mounted report as B.
  if(index!==1){
   assert.deepEqual(tuple(ready.report.effective_selection),expected);
   assert.deepEqual(tuple(session.mountedReport.effective_selection),expected);
   assert.equal(session.savedSelection,null);
  }else{
   assert.ok(firstNative,'A preparation evidence must precede existing-chat B');
   assert.equal(call.pid,firstNative.call.pid,'Warm B must use the original A worker PID');
   assert.equal(worker.startTicks,firstNative.worker.startTicks,'Worker PID start identity must not change');
   assert.equal(session.nativeId,firstNative.session.nativeId);
   assert.deepEqual(ready,firstNative.ready,'Warm B retains the actual original A-ready event');
   assert.deepEqual(session.mountedReport,firstNative.session.mountedReport,'Original A mounted report must remain the provenance');
   assert.deepEqual(tuple(ready.report.effective_selection),A);
   assert.equal(session.savedSelection.instance,expected.instance);
   assert.equal(session.savedSelection.model,expected.model);
   const applied=after.lifecycle.find(row=>row.kind==='runtime.nextMessageApplied'&&row.sessionId===session.id&&row.selection?.instance===expected.instance&&row.atNs<call.atNs);
   assert.ok(applied);
   assert.ok(acceptedConfiguration);
   assert.equal(session.nextMessageRevision,acceptedConfiguration.revision);
   assert.equal(applied.revision,acceptedConfiguration.revision);
   assert.equal(session.appliedConfigurationRevision,acceptedConfiguration.revision);
   await save('warm-b-configuration-join.json',{accepted:acceptedConfiguration,applied,
    nextMessageRevision:session.nextMessageRevision,appliedConfigurationRevision:session.appliedConfigurationRevision,
    inputId:receipt.inputId,generationId:finished.generation_id,call,controls:session.savedSelection});
  }
  const controlsBefore=after.providerControls.length;
  const controlReceipt=await action('runtime.control',{sessionId:session.id,operation:'configuration.providers',args:{}});
  assert.equal(controlReceipt.accepted,true);
  await expect.poll(async()=>{
   after=await proof();
   return after.providerControls.slice(controlsBefore).some(row=>row.sessionId===session.id);
  },{timeout:30000}).toBe(true);
  const providerControl=after.providerControls.slice(controlsBefore).find(row=>row.sessionId===session.id);
  assert.equal(providerControl.pid,call.pid);
  assert.deepEqual(tuple(providerControl.result.effective),expected);
  assert.equal(providerControl.result.pinned,index===1);
  await save(`send-${index}-provider-control.json`,{receipt:controlReceipt,observation:providerControl});
  await save(`send-${index}-generation-join.json`,{command,receipt,started,finished,call,worker,ready,session,providerControl});
  if(index===0)firstNative=structuredClone({call,worker,session,ready,providerControl});
  if(index===1)await save('warm-b-evidence.json',{originalA:firstNative,call,worker,session,ready,providerControl});
  await flush();return session.id;
 }
 await page.goto(url);
 await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 // Do not race first-use discovery with the measured composer journey.
 // In this fresh fixture the actual onboarding control, not a hidden view patch,
 // completes setup once a configured connection is visibly ready.
 const setupJourney=page.getByRole('region',{name:'Get started',exact:true});
 await expect(setupJourney.getByRole('heading',{name:'Your first connection is ready',exact:true})).toBeVisible();
 await discoveryRead();
 // Settings' optional routing catalog may fail under the audit guard. Retain
 // the real result; do not dispatch it again, install it, or treat admission as success.
 let routingRequest;
 await expect.poll(()=>{routingRequest=actions.find(row=>row.command.action==='routing.list'&&row.phase==='received');return !!routingRequest},{timeout:10000}).toBe(true);
 let routingProof;
 await expect.poll(async()=>{
  routingProof=await proof();
  return routingProof.routingDiscovery.some(row=>row.action==='routing.list'&&row.arguments.configurationTarget?.kind==='global'&&typeof row.result.routingCatalogError==='string');
 },{timeout:10000}).toBe(true);
 const routingRecords=routingProof.routingDiscovery.filter(row=>row.action==='routing.list'&&row.arguments.configurationTarget?.kind==='global');
 assert.equal(actions.filter(row=>row.command.action==='routing.list').length,1,'One observed optional routing request');
 assert.equal(routingRecords.length,1,'One actual optional routing result');
 const guard=routingProof.guardRefusals.find(row=>row.event==='subprocess.Popen'&&row.detail.includes('amplifier-bundle-routing-matrix'));
 assert.ok(guard,'Actual audit refusal retained');
 const routingCompletion=await completionFor(routingRequest.command.id,'routing.list',routingRequest.command.args,routingRequest.receipt,(await state()).client);
 await save('optional-routing-discovery.json',{request:routingRequest,completion:routingCompletion,result:routingRecords[0],guard,scope:'Optional Settings discovery; excluded from ordinary fixture root. Guard remains enforced.'});
 const setupClient=(await state()).client.id;
 const setupCompletion=page.waitForResponse(response=>{
  const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
  const command=request.postDataJSON();
  return command.action==='view.update'&&command.args?.patch?.setupJourney==='done'&&request.headers()['x-amplifier-client']===setupClient;
 },{timeout:10000});
 const setupDraft=page.waitForResponse(response=>{
  const request=response.request();return request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'&&
   request.postDataJSON().action==='session.draft'&&request.headers()['x-amplifier-client']===setupClient;
 },{timeout:10000});
 await setupJourney.getByRole('button',{name:'Start a chat',exact:true}).click();
 const setupResponses=await Promise.all([setupCompletion,setupDraft]);
 const setupReceipts=await Promise.all(setupResponses.map(async response=>({command:response.request().postDataJSON(),status:response.status(),receipt:await bounded(response.json(),10000,'Visible setup receipt')})));
 await save('onboarding-receipts.json',setupReceipts);
 for(const row of setupReceipts){assert.equal(row.status,200);assert.equal(row.receipt.accepted,true)}
 await expect(setupJourney).toBeHidden();
 await settleManagedDraft();
 const initial=await proof();await save('native-initial.json',initial);assert.equal(initial.calls.length,0);assert.equal(initial.readiness.importsReady,true);
 const originalSession=await send(A);
 await model.click();await showComposerOption(page,'provider');
 const configurePromise=page.waitForResponse(response=>{
  const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
  const command=request.postDataJSON();return command.action==='conversation.configure'&&command.args.sessionId===originalSession&&command.args.selection?.instance==='native-b';
 },{timeout:15000});
 await dialog.locator('#chat-provider').selectOption('native-b');
 const configured=await configurePromise,configureCommand=configured.request().postDataJSON(),configureReceipt=await bounded(configured.json(),10000,'Configuration receipt');
 await save('existing-chat-choice.json',{command:configureCommand,status:configured.status(),receipt:configureReceipt});
 assert.equal(configured.status(),200);assert.equal(configureReceipt.accepted,true);
 assert.equal(configureReceipt.result.revision,configureCommand.args.expectedRevision+1);
 acceptedConfiguration={revision:configureReceipt.result.revision,command:configureCommand,receipt:configureReceipt};
 await close();await visible(B);
 // Same-model/effort negative uses the SAME visible observer; no extra send.
 const countBefore=(await proof()).calls.length;
 await assert.rejects(()=>visible({...B,instance:'native-a'},300),error=>error.matcherResult?.name==='toHaveValue'&&error.matcherResult.expected==='native-a'&&error.matcherResult.actual==='native-b');
 await close();const countAfter=(await proof()).calls.length;
 await save('wrong-connection-negative.json',{expected:{...B,instance:'native-a'},actual:B,rejected:true,callsBefore:countBefore,callsAfter:countAfter});assert.equal(countAfter,countBefore);
 await send(B,originalSession);
 const sessionIds=[originalSession];
 for(let n=0;n<2;n++){
  await flush();const receipt=await action('session.draft');assert.equal(receipt.accepted,true);
  await settleManagedDraft();
  const id=await send(A);assert.ok(!sessionIds.includes(id));sessionIds.push(id);
 }
 const four=await proof();assert.equal(four.calls.length,4);await save('four-send-complete.json',four);
 // A distinct ordinary root adds native-alt at priority 0. Shared A=1/B=20
 // stay unchanged; no draft pin. Hold only this test's resolution boundary.
 assert.equal((await action('session.draft')).accepted,true);
 await settleManagedDraft();await visible(A);
 const alternate=four.readiness.alternateBundle.path;
 const armedResponse=await page.request.post(url+'/fixture/native-delay/arm',{timeout:10000});
 const armed=await armedResponse.json();await save('delayed-arm.json',{status:armedResponse.status(),armed});
 assert.equal(armedResponse.status(),200);assert.equal(armed.phase,'armed');assert.equal(armed.bundle,alternate);
 const draft=(await state()).view.newSessionDraft;
 const changed=await action('view.update',{patch:{newSessionDraft:{...draft,bundle:alternate,selection:{}}}});
 await save('delayed-draft-receipt.json',changed);assert.equal(changed.accepted,true);
 let held;
 await expect.poll(async()=>{
  held=await proof();await appendFile(evidence+'/delayed-observations.jsonl',JSON.stringify({phase:'waiting-held',proof:held})+'\n');
  return held.delayedProbe.phase;
 },{timeout:10000}).toBe('held');
 assert.ok(held.delayedProbe.probes.length>0);
 assert.ok(held.delayedProbe.probes.every(row=>row.phase==='held'&&!row.result));
 assert.equal(held.calls.length,4);
 await expect(model.locator(':scope > span')).toHaveText('Loading model…');
 await expect(model).toHaveAttribute('aria-busy','true');
 const waitingState=await state();
 assert.equal(waitingState.selectedSessionId,null);
 assert.equal(waitingState.view.newSessionDraft.bundle,alternate);
 assert.deepEqual(waitingState.view.newSessionDraft.selection,{});
 await page.screenshot({path:evidence+'/delayed-loading.png'});
 await save('delayed-loading.json',{chip:await model.locator(':scope > span').innerText(),
  busy:await model.getAttribute('aria-busy'),draft:waitingState.view.newSessionDraft,proof:held});
 const releasedResponse=await page.request.post(url+'/fixture/native-delay/release',{timeout:10000});
 const released=await releasedResponse.json();await save('delayed-release.json',{status:releasedResponse.status(),released});
 assert.equal(releasedResponse.status(),200);assert.equal(released.phase,'released');
 let resolved;
 await expect.poll(async()=>{
  resolved=await proof();await appendFile(evidence+'/delayed-observations.jsonl',JSON.stringify({phase:'waiting-ready',proof:resolved})+'\n');
  return resolved.delayedProbe.phase;
 },{timeout:60000}).toBe('ready');
 const readyProbe=resolved.delayedProbe.probes.find(row=>row.phase==='ready');
 assert.ok(readyProbe);assert.ok(readyProbe.releasedAtNs>=released.releasedAtNs);
 assert.ok(readyProbe.probeProcesses.length>0,'Releasing the gate must execute a real production probe subprocess');
 for(const process of readyProbe.probeProcesses){
  assert.ok(process.spawnedAtNs>=released.releasedAtNs&&process.spawnedAtNs<=readyProbe.finishedAtNs);
  assert.equal(process.command.at(-1),resolved.readiness.sources['draft_defaults_probe.py'].path);
 }
 assert.equal(readyProbe.result.bundle,alternate);assert.equal(readyProbe.result.globalOnly,true);
 assert.deepEqual(tuple(readyProbe.result.effective),ALT);
 assert.equal(resolved.calls.length,4,'Readiness must not perform inference');
 await visible(ALT);await save('delayed-ready.json',resolved);
 const alternateSession=await send(ALT);
 assert.ok(!sessionIds.includes(alternateSession));sessionIds.push(alternateSession);
 await flush();assert.deepEqual(pageErrors,[]);assert.deepEqual(evidenceErrors,[]);assert.deepEqual(assets.filter(row=>row.error),[]);
 const final=await proof();assert.equal(final.calls.length,5);
 assert.equal(final.sessions.find(row=>row.id===alternateSession).bundle,alternate);
 assert.ok(final.calls[4].atNs>readyProbe.finishedAtNs);
 assert.deepEqual(final.sharedSettings.current,final.sharedSettings.initial);
 assert.deepEqual(final.originRefusals,[]);
 await save('native-final.json',final);
 journey={outcome:'passed',mode:installed?'installed':'source',sends:5,sessions:sessionIds,sequence:[A,B,A,A,ALT],
  delayedBundle:{bundle:alternate,sessionId:alternateSession,loadingObserved:true,productionProbe:readyProbe},
  pageErrors,evidenceErrors,scope:'Real ordinary preparation and synthetic providers; low/high provider defaults, not native explicit xhigh or a literal unspecified CLI incident',staticResponses:assets.length};
 await save('journey.json',journey);
}catch(error){
 primary=error;const failure={primary:{name:error.name,message:error.message,stack:error.stack},pageErrors,evidenceErrors};
 const retainFailure=async()=>{try{await save('failure.json',failure)}catch(capture){cleanupErrors.push({stage:'failure-save',error:capture.message})}};
 await retainFailure();
 for(const [name,read] of [['native',()=>proof?.()],['discovery',()=>discoveryRead?.()],['receipts',async()=>{await flush?.();return actions}],['screenshot',async()=>{if(page)await page.screenshot({path:evidence+'/failure.png',timeout:3000});return 'failure.png'}]]){
  try{failure[name]=await bounded(Promise.resolve().then(read),15000,'Failure '+name)}catch(capture){failure[name]={error:capture.message}}
  await retainFailure();
 }
}finally{
 try{await bounded(browser?.close(),10000,'Browser close')}
 catch(error){primary??=error;cleanupErrors.push({stage:'browser-close',error:error.message})}
 finally{
  if(fixture.exitCode===null&&fixture.signalCode===null)fixture.kill('SIGTERM');
  let outcome;
  try{outcome=await bounded(exit,60000,'Owned fixture cleanup')}
  catch(error){
   primary??=error;outcome={unknown:true,error:error.message};fixture.kill('SIGKILL');
   try{outcome.forced=await bounded(exit,5000,'Owned fixture forced exit')}catch(forced){outcome.forcedError=forced.message}
  }
  try{
   await bounded(drained,5000,'Fixture streams');await logWrites;await save('fixture-exit.json',outcome);
   const cleanup=JSON.parse(await readFile(evidence+'/fixture/cleanup.json','utf8'));
   assert.equal(cleanup.complete,true);assert.deepEqual(cleanup.fallbackPids,[]);
   await save('cleanup-readback.json',cleanup);
   const origin=JSON.parse(await readFile(evidence+'/fixture/origin-policy.json','utf8'));
   const finalProof=JSON.parse(await readFile(evidence+'/fixture/final-proof.json','utf8'));
   assert.equal(origin.mode,installed?'installed':'source');
   const within=(base,path)=>under(resolve(base),resolve(path));
   const catalogProcesses=cleanup.processes.filter(row=>row.kind==='decision_account_catalog');
   assert.ok(catalogProcesses.length>0,'Real isolated account discovery must execute');
   for(const row of catalogProcesses){
    assert.equal(row.returncode,0);assert.equal(row.procAbsent,true);
    assert.deepEqual(row.script,origin.accountCatalog);
    assert.deepEqual(row.requestedCommand,[process.env.AMPLIFIER_TEST_PYTHON,'-I',origin.accountCatalog.path]);
    assert.deepEqual(row.command,[process.env.AMPLIFIER_TEST_PYTHON,'-I',origin.fixture.path,'--account-catalog',evidence+'/fixture']);
    const parent=cleanup.processes.find(value=>value.pid===row.parent.pid);
    assert.ok(parent,'Catalog belongs to a tracked worker/probe');
    assert.equal(parent.startTicks,row.parent.startTicks);assert.equal(parent.processGroup,row.processGroup);
    const guard=JSON.parse(await readFile(`${evidence}/fixture/guard-ready-${row.pid}.json`,'utf8'));
    assert.equal(guard.isolated,true);assert.deepEqual(guard.allowedCommands,[]);
    assert.deepEqual(guard.bootstrap,origin.fixture);
    assert.ok(guard.environmentNames.every(name=>['PATH','SYSTEMROOT','WINDIR','LANG','LC_ALL','TZ','TMPDIR','TEMP','TMP','LC_CTYPE'].includes(name)));
    assert.ok(guard.pythonPath.every(path=>!within(checkout,path)&&!within(evidence,path)),'Isolated helper must not inherit source/provider/bootstrap paths');
   }
   for(const row of cleanup.processes){
    const loaded=row.loadedSources;assert.ok(loaded&&!loaded.error);assert.deepEqual(loaded.violations,[]);
    assert.equal(loaded.python,process.env.AMPLIFIER_TEST_PYTHON);
    if(installed)assert.ok(within(origin.applicationRoot,loaded.script.path),'Worker/probe script must come from installed app');
    if(row.kind!=='runtime_worker')continue;
    const modules=loaded.modules;
    assert.match(modules['amplifier_core._engine']?.path||'',/\.so$/,'Hash the actual native Core engine binary');
    assert.ok(modules['amplifier_module_loop_live.orchestrator']?.sha256,'Hash loop-live implementation, not just __init__');
    assert.ok(Object.entries(modules).some(([name,value])=>name.startsWith('amplifier_foundation.')&&
     value.path.endsWith('.py')&&!value.path.endsWith('/__init__.py')),'Hash actual Foundation implementation files');
    assert.ok(modules['amplifier_module_loop_streaming']?.sha256,'Retain actually loaded streaming implementation and helpers');
    const boundaryText=await readFile(`${evidence}/fixture/loaded-boundaries-${row.pid}.jsonl`,'utf8');
    assert.ok(boundaryText.endsWith('\n'),'Never ignore a partial loaded-source record');
    const entries=boundaryText.trimEnd().split('\n').map(line=>JSON.parse(line)).filter(value=>value.boundary==='provider-entry');
    const calls=finalProof.calls.filter(value=>value.pid===row.pid);
    assert.deepEqual(entries.map(value=>value.call),calls,'Join source hashes to every actual provider entry');
    for(const entry of entries){
     assert.deepEqual(entry.violations,[]);
     for(const name of ['amplifier_core._engine','amplifier_module_loop_live.orchestrator','amplifier_module_loop_streaming']){
      assert.ok(entry.modules[name]?.sha256,'Missing dispatch-time implementation: '+name);
      assert.equal(entry.modules[name].sha256,finalProof.readiness.modules[name].sha256,name+' differs from prepared server dependency');
     }
     assert.ok(Object.entries(entry.modules).some(([name,value])=>name.startsWith('amplifier_foundation.')&&
      value.path.endsWith('.py')&&!value.path.endsWith('/__init__.py')));
    }
    if(installed)for(const [name,value] of Object.entries(modules)){
     const roots=name==='amplifier_module_provider_native_fixture'?[origin.providerRoot]:
      name==='amplifier_web'||name.startsWith('amplifier_web.')?[origin.applicationRoot]:origin.dependencyPrefixes;
     assert.ok(roots.some(prefix=>within(prefix,value.path)),'Unapproved loaded origin: '+name);
    }
   }
   await save('loaded-origin-readback.json',{mode:origin.mode,processes:cleanup.processes.map(row=>({
    pid:row.pid,kind:row.kind,loadedSources:row.loadedSources}))});
  }
  catch(error){primary??=error;cleanupErrors.push({stage:'fixture-retention',error:error.message})}
  if(outcome.code!==0){primary??=Error('Fixture cleanup did not exit cleanly: '+JSON.stringify(outcome));}
 }
}
if(primary){console.error(JSON.stringify({primary:primary.message,cleanupErrors}));throw primary}
await save('result.json',{...journey,cleanupErrors,fixtureClosed:true});
console.log('Native provider boundary passed: shared A -> same-worker B -> shared A -> shared A -> delayed root-local native-alt; real preparation, production probe and provider entry.');
