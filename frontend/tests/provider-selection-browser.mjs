// Real bundled UI -> creation -> synthetic provider dispatch through the native selector.
import {showComposerOption} from './composer-options-helpers.mjs';
import {ownFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {appendFile,mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
const fixturePath=process.env.PROVIDER_SELECTION_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fixturePath,'--chat-controls','--provider-resolution'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Provider selection fixture',timeoutMs:3000});
const fixtureEvents={exit:null,close:null};
fixture.once('exit',(code,signal)=>{fixtureEvents.exit={pid:fixture.pid,code,signal}});
fixture.once('close',(code,signal)=>{fixtureEvents.close={pid:fixture.pid,code,signal}});
const evidence=artifacts+'/provider1201';
const save=async(name,value)=>writeFile(`${evidence}/${name}`,JSON.stringify(value,null,2));
async function bounded(promise,ms,label){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),ms)})])}
 finally{clearTimeout(timer)}
}
let browser,page,captureFailure,primaryError;
try{
 await mkdir(evidence,{recursive:true});
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:900},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();
 page.setDefaultTimeout(5000);
 const errors=[],evidenceErrors=[];page.on('pageerror',error=>errors.push(error.message));
 const actionRows=[],actionRequests=new Map(),actionSettlements=[];
 let actionWrites=Promise.resolve();
 const recordAction=event=>{
  actionWrites=actionWrites.then(()=>appendFile(`${evidence}/actions.jsonl`,JSON.stringify(event)+'\n'))
   .catch(error=>{evidenceErrors.push(error.message)});
 };
 await writeFile(`${evidence}/actions.jsonl`,'');
 const observedCommand=request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return null;
  const command=request.postDataJSON();
  return ['providers.models','providers.list','configuration.defaults'].includes(command?.action)||
   command?.action==='view.update'&&(command.args?.patch?.newSessionDraft||command.args?.patch?.composerModel)?command:null;
 };
 page.on('request',request=>{
  const command=observedCommand(request);if(!command)return;
  const row={sequence:actionRows.length,observedAt:Date.now(),command,clientId:request.headers()['x-amplifier-client'],phase:'sent'};
  actionRows.push(row);actionRequests.set(request,row);recordAction({...row});
 });
 page.on('response',response=>{
  const row=actionRequests.get(response.request());if(!row)return;
  actionSettlements.push(bounded(response.json(),5000,'Action receipt '+row.command.id).then(receipt=>{
   Object.assign(row,{phase:'received',receivedAt:Date.now(),status:response.status(),receipt});recordAction({...row});
  }).catch(error=>{row.phase='receipt-unavailable';row.error=error.message;recordAction({...row})}));
 });
 page.on('requestfailed',request=>{
  const row=actionRequests.get(request);if(!row)return;
  Object.assign(row,{phase:'transport-failed',error:request.failure()});recordAction({...row});
 });
 const staticManifest=process.env.PROVIDER_SELECTION_STATIC_MANIFEST?JSON.parse(await readFile(process.env.PROVIDER_SELECTION_STATIC_MANIFEST,'utf8')):null;
 const consumedResponses=[],consumedAssets=[];
 let assetWrites=Promise.resolve();
 function recordAsset(row){
  consumedAssets.push(row);
  assetWrites=assetWrites.then(()=>appendFile(`${evidence}/consumed-assets.jsonl`,JSON.stringify(row)+'\n'))
   .catch(error=>{evidenceErrors.push(error.message)});
 }
 await writeFile(`${evidence}/consumed-assets.jsonl`,'');
 if(staticManifest)page.on('response',response=>{
  const address=new URL(response.url());
  if(address.origin!==new URL(url).origin)return;
  const path=address.pathname==='/'?'index.html':address.pathname.slice(1);
  const expected=staticManifest['amplifier_web/static/'+path];
  const staticResponse=expected||path.startsWith('assets/')||
   (!path.startsWith('api/')&&['script','stylesheet','image','font','media'].includes(response.request().resourceType()));
  if(!staticResponse)return;
  consumedResponses.push((async()=>{
   assert.ok(expected,'Unmanifested static response: '+path);
   assert.equal(response.status(),200,path);
   const sha256=createHash('sha256').update(await bounded(response.body(),5000,'Static response '+path)).digest('hex');
   assert.equal(sha256,expected,path);
   return {path,sha256};
  })().catch(error=>({path,error:error.message})).then(row=>{recordAsset(row);return row}));
 });
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const selectedBoundary=value=>({
  client:value.client,revision:value.revision,selectedSessionId:value.selectedSessionId,
  configurationRevision:value.configurationRevision,savedDraft:value.view?.newSessionDraft,
  composerModel:value.view?.composerModel,catalog:value.setup?.providerCatalogs?.unified??null,
  providersWorkspace:value.setup?.providersWorkspace,providersRequestedWorkspace:value.setup?.providersRequestedWorkspace,
  providersLocation:value.setup?.providersLocation,
  actions:Object.fromEntries(['providers.models','providers.list','configuration.defaults'].map(name=>[name,value.actionStatus?.[name]??null])),
 });
 async function serverBoundary(label,clientId){
  const address=url+'/api/state?'+new URLSearchParams({clientId});
  const response=await page.request.get(address,{timeout:5000});
  const body=await response.json();
  const row={label,observedAt:Date.now(),request:{method:'GET',path:'/api/state',clientId},status:response.status(),state:selectedBoundary(body)};
  await appendFile(`${evidence}/server-boundaries.jsonl`,JSON.stringify(row)+'\n');
  assert.equal(response.status(),200);assert.equal(body.client.id,clientId);
  return row;
 }
 const requests=async(timeout=5000)=>{
  const response=await page.request.get(url+'/fixture/provider-resolution',{timeout});
  assert.equal(response.status(),200);
  return (await response.json()).requests;
 };
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const dialog=page.getByRole('dialog',{name:'Model for this chat',exact:true});
 const connectionLabel=instance=>`Synthetic provider (${instance})`;
 const labelFor=expected=>expected.effort?`${expected.model} (${expected.effort})`:expected.model;
 const beforeSends=[],displayObservations=[];
 const activate=locator=>page.viewportSize().width<600?locator.tap():locator.click();
 async function closeOptions(){
  await activate(dialog.getByRole('button',{name:'Close chat options',exact:true}));
  await expect(dialog).toBeHidden();
  await expect(model).toHaveAttribute('aria-expanded','false');
 }
 // Observe only controls currently visible to a person. Native title is owned
 // by the tooltip layer and is neither durable nor the connection selector.
 async function assertDisplayed(expected,{manual=false,...options}={}){
  const observed={expected,viewport:page.viewportSize()};
  if(!await dialog.isVisible())await activate(model);
  await expect(dialog).toBeVisible();
  await showComposerOption(page,'provider');
  const provider=dialog.locator('select#chat-provider');
  await expect(provider).toBeVisible();
  observed.instance=await provider.inputValue();
  observed.connectionLabel=await provider.evaluate(node=>node.selectedOptions[0]?.label);
  displayObservations.push(observed);
  await save('display-observations.json',displayObservations);
  await expect(provider,'Visible connection ID').toHaveValue(expected.instance,options);
  await expect(provider.locator('option:checked'),'Visible connection label').toHaveText(connectionLabel(expected.instance),options);
  observed.instance=await provider.inputValue();
  observed.connectionLabel=await provider.evaluate(node=>node.selectedOptions[0]?.label);
  assert.equal(observed.instance,expected.instance);
  assert.equal(observed.connectionLabel,connectionLabel(expected.instance));
  await save('display-observations.json',displayObservations);
  await showComposerOption(page,'model');
  const selectedModel=dialog.locator(manual?'input#chat-model':'select#chat-model');
  await expect(selectedModel).toBeVisible();
  await expect(selectedModel).toHaveValue(expected.model,options);
  if(!manual)await expect(selectedModel.locator('option:checked')).toHaveText(expected.model,options);
  observed.model=await selectedModel.inputValue();
  observed.modelLabel=await selectedModel.evaluate(node=>node.selectedOptions?node.selectedOptions[0]?.label:node.value);
  assert.equal(observed.model,expected.model);
  assert.equal(observed.modelLabel,expected.model);
  await save('display-observations.json',displayObservations);
  await showComposerOption(page,'effort');
  const effort=dialog.locator('#chat-effort');
  await expect(effort).toBeVisible();
  await expect(effort).toHaveAttribute('aria-valuetext',expected.effort,options);
  await expect(dialog.locator('label[for="chat-effort"] strong')).toHaveText(expected.effort,options);
  observed.effort=await effort.getAttribute('aria-valuetext');
  assert.equal(observed.effort,expected.effort);
  await save('display-observations.json',displayObservations);
  await closeOptions();
  await expect(model.locator(':scope > span')).toHaveText(labelFor(expected),options);
  observed.chip=await model.locator(':scope > span').innerText();
  observed.dismissed=true;
  await save('display-observations.json',displayObservations);
  return observed;
 }
 captureFailure=async error=>{
  const report={primary:{name:error.name,message:error.message,stack:error.stack},pageErrors:errors,evidenceErrors};
  await save('failure.json',report);
  for(const [name,read] of [
   ['dispatch',()=>requests(1500)],
   ['chip',()=>model.innerText({timeout:1500})],
   ['visibleOptions',async()=>await dialog.isVisible()?dialog.innerText({timeout:1500}):null],
   ['visibleControls',()=>dialog.locator('select:visible,input:visible').evaluateAll(nodes=>nodes.map(node=>({
    id:node.id,value:node.value,label:node.selectedOptions?.[0]?.label,effort:node.getAttribute('aria-valuetext')})))],
   ['screenshot',async()=>{await page.screenshot({path:`${evidence}/failure.png`,timeout:2000});return 'failure.png'}],
   ['assets',async()=>{await bounded(Promise.all(consumedResponses),2000,'Pending static responses');await assetWrites;return consumedAssets}],
   ['actions',async()=>{await bounded(Promise.all(actionSettlements),2000,'Pending action receipts');await actionWrites;return actionRows}],
  ]){
   try{report[name]=await bounded(Promise.resolve().then(read),2500,name)}
   catch(failure){report[name]={captureError:failure.message}}
   await save('failure.json',report);
  }
 };
 const inherited={instance:'cli',model:'cli-model',effort:'low'};
 let count=0;
 async function send(expected,options={}){
  const text='Synthetic first request '+count;
  await composer.fill(text);
  // Inspect the rendered destination immediately before Enter, independently
  // of the provider's later complete-call recording.
  const before={expected,display:await assertDisplayed(expected,options),callsBefore:(await requests()).length};
  await expect(composer).toHaveText(text);
  before.draftText=await composer.innerText();
  beforeSends.push(before);
  await save('before-sends.json',beforeSends);
  await save(`send-${count+1}-before.json`,before);
  await composer.press('Enter');
  await expect.poll(async()=>(await requests()).length).toBe(++count);
  const dispatched=await requests(),last=dispatched.at(-1);
  await save('dispatch.json',dispatched);
  await save(`send-${count}-dispatch.json`,last);
  assert.deepEqual(last.dispatch,expected);assert.deepEqual(last.effective,expected);
  await expect.poll(async()=>{const current=await state();return current.sessions.find(row=>row.id===last.sessionId)?.status}).toBe('idle');
  await expect(model.locator(':scope > span')).toHaveText(labelFor(expected));
  return last;
 }
 await page.goto(url);
 if(staticManifest){
  const expected=staticManifest,verified={};
  const assetUrls=await page.locator('script[src],link[rel="modulepreload"],link[rel="stylesheet"]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('src')||node.getAttribute('href')));
  for(const path of ['index.html','build.json',...assetUrls.map(path=>path.startsWith('/')?path.slice(1):path)]){
   const response=await page.request.get(url+'/'+path);
   assert.equal(response.status(),200,path);
   const sha=createHash('sha256').update(await response.body()).digest('hex');
   assert.equal(sha,expected['amplifier_web/static/'+path],path);
   verified[path]=sha;
  }
  await save('served-assets.json',verified);
 }
 await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 await assertDisplayed(inherited);
 // Deliberately choose the other real synthetic connection with the SAME model
 // and effort. The normal visible-control observer must reject its connection.
 await activate(model);await showComposerOption(page,'provider');
 await dialog.locator('select#chat-provider').selectOption('unified');
 await showComposerOption(page,'model');await dialog.locator('select#chat-model').selectOption('cli-model');
 await showComposerOption(page,'effort');
 await dialog.locator('#chat-effort').focus();await dialog.locator('#chat-effort').press('Home');await dialog.locator('#chat-effort').press('Tab');
 await closeOptions();
 const wrong={...inherited,instance:'unified'};
 const wrongDisplay=await assertDisplayed(wrong);
 await assert.rejects(()=>assertDisplayed(inherited,{timeout:250}),error=>
  error.matcherResult?.name==='toHaveValue'&&error.matcherResult.expected==='cli'&&error.matcherResult.actual==='unified');
 // The expected rejection leaves the provider page open. Dismiss normally.
 await closeOptions();
 await expect(model.locator(':scope > span')).toHaveText(labelFor(wrong));
 const negative={expected:inherited,observed:wrongDisplay,rejected:true,calls:(await requests()).length};
 await save('wrong-connection-negative.json',negative);
 assert.equal(negative.calls,0);
 // Keep the strictly observed choice. Selecting the provider again deliberately
 // resets model and effort; exercise that separately, never in positive setup.
 await assertDisplayed(wrong);
 const negativeClient=(await state()).client.id;
 let baselineCommand;
 await expect.poll(()=>{
  baselineCommand=[...actionRows].reverse().find(row=>row.clientId===negativeClient&&
   row.command.action==='view.update'&&JSON.stringify(row.command.args?.patch?.newSessionDraft?.selection)===JSON.stringify(wrong));
  return baselineCommand?.phase;
 },{timeout:5000}).toBe('received');
 assert.equal(baselineCommand.receipt.accepted,true);
 const negativeBaseline=await serverBoundary('negative-baseline',negativeClient);
 negativeBaseline.selectionReceipt=baselineCommand;
 if(baselineCommand.receipt.stateRevision!==undefined){
  assert.equal(negativeBaseline.state.client.hostInstanceId,baselineCommand.receipt.hostInstanceId);
  assert.ok(negativeBaseline.state.revision>=baselineCommand.receipt.stateRevision);
 }
 assert.deepEqual(negativeBaseline.state.savedDraft.selection,wrong);
 await model.click();
 await showComposerOption(page,'effort');
 const slider=page.getByRole('slider');await slider.focus();await slider.press('End');await slider.press('Tab');
 await closeOptions();
 const explicit={instance:'unified',model:'cli-model',effort:'xhigh'};
 await assertDisplayed(explicit);
 await page.reload();await assertDisplayed(explicit);
 assert.deepEqual((await send(explicit)).selection,explicit);
 // Existing-chat choice does not become a hidden new-chat default, including consecutive chats.
 for(let index=0;index<2;index++){
  await action('session.draft');await assertDisplayed(inherited);
  assert.ok(!(await send(inherited)).selection);
 }
 await action('session.draft');await assertDisplayed(inherited);
 const before=await state(),setup=before.view.newSessionDraft;
 await action('view.update',{patch:{newSessionDraft:{...setup,bundle:'alternate',selection:{}}}});
 // The alternate probe is deliberately held. A prior bundle must not masquerade as resolved.
 await expect(model.locator(':scope > span')).toHaveText('Loading model…');
 await expect(model).toHaveAttribute('aria-busy','true');
 // Discovery is busy, not disabled: retain undimmed text and keyboard access.
 await expect(model).toBeEnabled();
 await expect(model).toHaveCSS('opacity','1');
 await expect(model).toHaveCSS('cursor','progress');
 await model.focus();await model.press('Enter');
 await expect(dialog).toBeVisible();
 await expect(dialog.locator('#chat-provider')).toBeFocused();
 await closeOptions();
 await expect(model).toBeFocused();
 await expect(model).toHaveAttribute('aria-busy','true');
 await page.screenshot({path:artifacts+'/provider1201/desktop-loading.png'});
 assert.equal((await requests()).length,count);
 await page.request.post(url+'/fixture/provider-resolution/release');
 await assertDisplayed({instance:'unified',model:'unified-model',effort:'high'});
 await page.setViewportSize({width:390,height:844});
 await model.tap();await showComposerOption(page,'provider');
 await expect(page.locator('select#chat-provider')).toHaveValue('unified');
 await page.screenshot({path:artifacts+'/provider1201/narrow-provider.png'});
 await closeOptions();
 assert.ok(!(await send({instance:'unified',model:'unified-model',effort:'high'})).selection);
 if(staticManifest){
  const consumed=await bounded(Promise.all(consumedResponses),6000,'Static response checks');
  await assetWrites;
  assert.deepEqual(consumed.filter(row=>row.error),[]);
  assert.ok(consumed.some(row=>row.path==='index.html'));
  assert.ok(consumed.some(row=>row.path.startsWith('assets/app-')&&row.path.endsWith('.js')));
  await save('consumed-assets.json',consumed);
 }
 assert.deepEqual(evidenceErrors,[]);
 assert.deepEqual(errors,[]);
 await save('positive-journey.json',{completed:true,sends:count,pageErrors:errors,evidenceErrors});
 // The former diagnostic mocked cached_probe and could pass with missing
 // options. These are acceptance assertions over the real shared host cache.
 const probeCounts=async()=>(await (await page.request.get(url+'/fixture/provider-resolution')).json()).probes;
 assert.deepEqual(await probeCounts(),{cli:1,unified:1},'All chats/drafts share exactly one discovery per connection');
 const sentBefore=(await requests()).length;
 await action('session.draft');
 await action('view.update',{patch:{newSessionDraft:negativeBaseline.state.savedDraft}});
 await assertDisplayed(wrong);
 await activate(model);
 await dialog.locator('#chat-provider').selectOption('unified');
 await expect(dialog.locator('select#chat-model option[value="cli-model"]')).toHaveCount(1);
 await expect(dialog.locator('select#chat-model option[value="unified-model"]')).toHaveCount(1);
 assert.deepEqual((await state()).view.newSessionDraft.selection,wrong,'Changing the local connection field does not silently save a default model');
  // Successful recommendations are not an entitlement gate. The uncommon exact
  // ID path stays explicit and does not add a made-up row to the API inventory.
  await dialog.getByRole('button',{name:'Enter an exact model ID',exact:true}).click();
  await dialog.locator('input#chat-model').fill('hidden-by-recommendation');
  await dialog.locator('input#chat-model').press('Tab');
  await expect.poll(async()=>(await state()).view.newSessionDraft.selection?.model).toBe('hidden-by-recommendation');
  await expect(dialog.locator('select#chat-model-catalog option[value="hidden-by-recommendation"]')).toHaveCount(0);
  await expect(dialog.getByText(/not verified by discovery/)).toBeVisible();
  await dialog.locator('input#chat-model').fill(wrong.model);
  await dialog.locator('input#chat-model').press('Tab');
  await expect.poll(async()=>(await state()).view.newSessionDraft.selection?.model).toBe(wrong.model);
  // Choosing a different model intentionally clears model-specific effort.
  // Restore the explicit old journey baseline rather than assuming it survived.
  await showComposerOption(page,'effort');
  await dialog.locator('#chat-effort').focus();await dialog.locator('#chat-effort').press('Home');await dialog.locator('#chat-effort').press('Tab');
  await expect.poll(async()=>(await state()).view.newSessionDraft.selection).toEqual(wrong);
 const fault=async mode=>{const response=await page.request.post(url+'/fixture/provider-resolution/fault',{data:{id:'unified',mode}});assert.equal(response.status(),200)};
 await fault('error');
 await dialog.getByRole('button',{name:'Refresh this connection',exact:true}).click();
 await expect(dialog.getByText(/discovery failed at/)).toBeVisible();
 await expect(dialog.locator('select#chat-model-catalog option[value="cli-model"]')).toHaveCount(1);
 await expect(dialog.getByText(/Last successful refresh/)).toBeVisible();
 assert.deepEqual(await probeCounts(),{cli:1,unified:2},'Refresh is scoped, failure preserves the authentic list');
 await closeOptions();await page.reload();await activate(model);
 await expect(dialog.locator('input#chat-model')).toBeVisible();
 assert.deepEqual(await probeCounts(),{cli:1,unified:2},'Reopen/reload does not retry failure');
 await dialog.locator('input#chat-model').fill('manual-deployment');
 await dialog.locator('input#chat-model').press('Tab');
 await expect.poll(async()=>(await state()).view.newSessionDraft.selection?.model).toBe('manual-deployment');
 await dialog.evaluate(node=>{node.scrollTop=0});
 await page.screenshot({path:`${evidence}/narrow-manual.png`});
 await closeOptions();
 await send({instance:'unified',model:'manual-deployment',effort:wrong.effort}, {manual:true});
 await activate(model);
 await fault('empty');
 await dialog.getByRole('button',{name:'Refresh this connection',exact:true}).click();
 await expect(dialog.getByText('This connection returned no models.',{exact:true})).toBeVisible();
 assert.equal(await dialog.locator('select#chat-model-catalog option[value="cli-model"]').count(),0);
 await expect(dialog.getByText(/Saved selection: manual-deployment/)).toBeVisible();
 assert.deepEqual(await probeCounts(),{cli:1,unified:3});
 assert.equal((await requests()).length,sentBefore+1);
 await page.setViewportSize({width:1280,height:900});
 await dialog.evaluate(node=>{node.scrollTop=0});
 await page.screenshot({path:`${evidence}/desktop-empty.png`});
 await closeOptions();
 await save('shared-catalog.json',{completed:true,probes:await probeCounts(),manualDispatch:(await requests()).at(-1)});
 const originChecks=[];
 for(const [origin,label] of [
  ['configured','Configured models — not checked against the service.'],
  ['static','Static recommendations — not a live inventory.'],
  ['simulated','Simulated models — test responses only.']
 ]){
  const response=await page.request.post(url+'/fixture/provider-resolution/fault',{data:{id:'unified',mode:'ready',origin}});
  assert.equal(response.status(),200);
  await activate(model);
  await dialog.getByRole('button',{name:'Refresh this connection',exact:true}).click();
  await expect(dialog.getByText(label,{exact:true})).toBeVisible();
  await expect(dialog.locator('option[value="unified-model"]')).toHaveCount(1);
  await expect(dialog.getByText(/Saved selection: manual-deployment.*catalog/)).toBeVisible();
  for(const width of [1280,390]){
   await page.setViewportSize({width,height:900});
   await expect(dialog.getByText(label,{exact:true})).toBeVisible();
   assert.equal(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth+1),true,'Origin label must wrap without horizontal overflow');
   await page.screenshot({path:`${evidence}/origin-${origin}-${width}.png`});
  }
  await fault('error');
  await dialog.getByRole('button',{name:'Refresh this connection',exact:true}).click();
  await expect(dialog.getByText(/discovery failed at/)).toBeVisible();
  await expect(dialog.getByText(label,{exact:true})).toBeVisible();
  await expect(dialog.locator('option[value="unified-model"]')).toHaveCount(1);
  await fault('empty');
  await dialog.getByRole('button',{name:'Refresh this connection',exact:true}).click();
  await expect(dialog.getByText('This connection returned no models.',{exact:true})).toBeVisible();
  await expect(dialog.getByText(label,{exact:true})).toBeVisible();
  const current=await state();
  assert.equal(current.sessions.find(row=>row.id===current.selectedSessionId)?.selection?.model,'manual-deployment');
  originChecks.push({origin,label,ready:true,stale:true,empty:true,widths:[1280,390]});
  await closeOptions();
 }
 assert.equal((await requests()).length,sentBefore+1,'Browsing local-origin lists must not send inference');
 await save('origin-labels.json',{completed:true,checks:originChecks,syntheticFixture:true});
 await bounded(Promise.all(actionSettlements),5000,'Final action receipts');await actionWrites;
 await save('actions.json',actionRows);
 console.log('Provider selection passed: exact pre-send connection/model/effort; wrong-connection label rejected; explicit draft+effort+reload; consecutive inherited chats; delayed bundle truth; narrow touch; synthetic native-selector complete-call recording (not full prepare_manager).');
}catch(error){
 primaryError=error;
 try{
  if(captureFailure)await bounded(captureFailure(error),14000,'Failure capture');
  else await save('failure.json',{primary:{name:error.name,message:error.message,stack:error.stack}});
 }catch(captureError){console.error('Secondary evidence capture failure:',captureError)}
 throw error;
}finally{
 const cleanupErrors=[];
 let fixtureStopped=false,browserClosed=false;
 try{await bounded(browser?.close(),3000,'Browser close');browserClosed=!browser?.isConnected()}catch(error){cleanupErrors.push(error)}
 try{await stopFixture();fixtureStopped=true}catch(error){cleanupErrors.push(error)}
 try{await save('cleanup.json',{browserClosed,fixtureStopped,...fixtureEvents,
  errors:cleanupErrors.map(error=>({name:error.name,message:error.message}))})}
 catch(error){cleanupErrors.push(error)}
 if(cleanupErrors.length){
  if(primaryError)console.error('Secondary teardown failures (primary failure preserved):',cleanupErrors);
  else throw new AggregateError(cleanupErrors,'Fixture teardown failed');
 }
}
