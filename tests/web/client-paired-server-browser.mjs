// Integration: actual source server + installed independent web artifact, or
// explicit installed-wheel mode. Only the Python host gets a Node-free PATH.
// Only runtime/providers are synthetic. Fixture selection forces the real SSE
// path; server build/version metadata, auth, actions and history are untouched.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {isAbsolute,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const [serverRoot,recordPath,stateParent]=process.argv.slice(2);
if(!serverRoot||!recordPath||!stateParent||!process.env.AMPLIFIER_TEST_PYTHON)throw Error('Supply server root, assembly record, state parent and AMPLIFIER_TEST_PYTHON');
const record=JSON.parse(await readFile(recordPath)),manifest=record.client.manifest;
const installedPackage=process.env.AMPLIFIER_TEST_INSTALLED_PACKAGE;
const qualification=installedPackage?'actual-installed-wheel-with-bundled-web-assets':
 'actual-source-server-with-installed-independent-web-artifact';
const hostEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1'};
if(installedPackage){
 assert.ok(isAbsolute(installedPackage),'Installed package must be explicitly absolute');
 assert.ok(isAbsolute(process.env.AMPLIFIER_TEST_PYTHON),'Installed fixture requires its exact Python executable');
 assert.ok(process.env.AMPLIFIER_TEST_HOST_PATH&&isAbsolute(process.env.AMPLIFIER_TEST_HOST_PATH),
  'Installed fixture requires an explicit Node-free host PATH directory');
 for(const key of Object.keys(hostEnv))if(['PATH','PYTHONPATH'].includes(key.toUpperCase()))delete hostEnv[key];
 hostEnv.PATH=process.env.AMPLIFIER_TEST_HOST_PATH;
}
if(process.env.AMPLIFIER_TEST_CLIENT_REVISION)
 assert.equal(manifest.source.revision,process.env.AMPLIFIER_TEST_CLIENT_REVISION,'Qualify the exact selected client, not a later merge');
const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||join(stateParent,'paired-browser');
await mkdir(artifact,{recursive:true});
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

async function qualifyLegacyNotice({page,composer,history,actions,privateRequests,screenshot}){
 const payloadKey='amplifier.messageOutbox.v1',metadataKey='amplifier.legacyOutboxNotice.v1';
 const text='This tab retains earlier message copies with no verified host identity. Their delivery is unknown. Nothing was sent again.';
 const draft='Unfinished paired-server draft';
 const original=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const action=async(name,args)=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result.accepted,true);return result};
 // Create/select through the actual host, never a substituted state response.
 // Only notice presentation is new coverage; the existing send/reconnect journey
 // above and message-delivery-browser own delivery/retry acceptance.
 await action('session.create',{title:'Retained-copy navigation control'});
 const other=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.notEqual(other,original);
 await action('session.select',{id:original});
 await expect(composer).toHaveText(draft);
 const before=await history(),actionStart=actions.length;
 const retained=[
  {id:'paired-unbound-unknown',commandId:'paired-unbound-input',sessionId:original,createdAt:123,
   text:'paired-private-retained-unknown',status:'unknown',attachmentIds:['paired-private-retained-attachment'],
   attachments:[{id:'paired-private-retained-attachment',name:'paired-private-retained-reference.txt',
    url:'/api/attachments/paired-private-retained-attachment',size:19}],
   document:{version:2,segments:[{type:'text',text:'paired-private-retained-document'}]}},
  {id:'paired-unbound-failed',commandId:'paired-unbound-failure',sessionId:other,createdAt:124,
   text:'paired-private-retained-failed',status:'failed',attachmentIds:[]},
 ];
 const serialize=rows=>'\n'+JSON.stringify(rows,null,2)+'\n';
 let raw=serialize(retained);
 const stored=()=>page.evaluate(([payload,metadata])=>({
  raw:sessionStorage.getItem(payload),metadata:sessionStorage.getItem(metadata),
 }),[payloadKey,metadataKey]);
 assert.deepEqual(JSON.parse((await stored()).raw||'[]'),[],'Accepted sends left no pending local input');
 // Fixture-only seed in this fresh owned context. There is intentionally no
 // scope: matching a real chat ID does not establish a verified host binding.
 await page.evaluate(([payload,metadata,value])=>{
  sessionStorage.setItem(payload,value);sessionStorage.removeItem(metadata);
 },[payloadKey,metadataKey,raw]);
 const notice=page.locator('[data-part="legacy-outbox-notice"]'),button=notice.getByRole('button');
 async function check(expanded){
  await expect(notice).toBeVisible();await expect(button).toHaveCount(1);
  await expect(button).toHaveAttribute('type','button');
  await expect(button).toHaveAttribute('aria-expanded',String(expanded));
  await expect(button).toHaveAccessibleName(expanded?'Hide notice':'Retained copies (2)');
  if(expanded)await expect(notice.getByRole('status')).toHaveText(text);
  else await expect(notice.getByText(text,{exact:true})).toHaveCount(0);
  assert.equal((await stored()).raw,raw,'Notice operations preserve the exact retained payload bytes');
  assert.equal(await page.locator('body').evaluate(node=>node.textContent.includes('paired-private-retained-')),false);
  assert.deepEqual(privateRequests,[],'No retained message, attachment or document is fetched or posted');
  assert.deepEqual(actions.slice(actionStart).filter(row=>row.action.startsWith('conversation.')||
   ['message.edit','session.delete','session.archive','attachment.add','attachment.remove'].includes(row.action)),[],
  'Notice operations do not check, resend, edit or delete retained work');
  assert.deepEqual(await history(),before,'Notice presentation and selection preserve canonical messages and runtime counters');
 }
 async function reload(expanded){
  await page.reload();await showFixtureConversation(page);await check(expanded);
  await expect(composer).toHaveText(draft);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(draft);
 }
 async function toggle(expanded,key){
  const handle=await button.elementHandle();
  try{
   if(key){await button.focus();await page.keyboard.press(key)}else await button.click();
   await check(expanded);await expect(composer).toHaveText(draft);
   assert.ok(await handle.evaluate(node=>node.isConnected&&node===document.querySelector('[data-part="legacy-outbox-notice"] button')),
    'The same button remains discoverable for reopen');
  }finally{await handle?.dispose()}
 }
 await reload(true);await screenshot('legacy-expanded');
 await toggle(false);
 const metadata=(await stored()).metadata;
 assert.ok(metadata&&metadata.includes('paired-unbound-unknown'));
 assert.ok(!metadata.includes('paired-private-retained-'),'Notice metadata contains no payload');
 await screenshot('legacy-hidden');await reload(false);
 await toggle(true,'Enter');await toggle(false,'Space');
 await action('session.select',{id:other});await check(false);
 await action('session.select',{id:original});await check(false);
 await expect(composer).toHaveText(draft);
 retained[0]={...retained[0],id:'paired-new-unbound-identity'};
 raw=serialize(retained); // Same count, new identity must not inherit dismissal.
 await page.evaluate(([key,value])=>sessionStorage.setItem(key,value),[payloadKey,raw]);
 assert.equal((await stored()).metadata,metadata);
 await reload(true);await screenshot('legacy-new-row');
 assert.equal((await stored()).metadata,metadata,'Resurfacing is not a journal or metadata rewrite');
 return {passed:true,retainedCount:2,payloadBytesPreserved:true,privatePayloadNeverRequested:true,
  hideReopenReload:true,chatSwitchPreserved:true,sameCountNewIdentityResurfaced:true,
  draftPreserved:true,extraSendsRetriesEditsDeletes:0,canonicalHistoryPreserved:true};
}

const {base,stop}=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,
 args:[...(installedPackage?['-I','-B']:[]),fileURLToPath(new URL('../../tests/fixtures/client_paired_server.py',import.meta.url)),
  '--server-root',serverRoot,'--assembly-record',recordPath,'--state-parent',stateParent],
 env:hostEnv,tag:'client-paired-server',name:'Paired web server'});
let browser,failure,page,result;
const screenshots=[];
const screenshot=async name=>{const path=join(artifact,name+'.png');await page.screenshot({path});screenshots.push(path)};
try{
 const unauthenticated=await fetch(base+'/api/state');assert.equal(unauthenticated.status,401);
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();
 const errors=[],sends=[],actions=[],privateRequests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if((request.url()+(request.postData()||'')).includes('paired-private-retained-'))privateRequests.push('retained-payload-request');
  if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){
   const envelope=request.postDataJSON(),body=envelope.action==='shell.command'?(envelope.args.command||envelope.args):envelope;
   actions.push(body);if(body.action==='conversation.send')sends.push(body);
  }
 });
 await page.route('**/api/clients/attach',async route=>{
  const response=await route.fetch(),attachment=await response.json();
  await route.fulfill({response,json:{...attachment,transports:['sse']}});
 });
 await page.addInitScript(()=>{
  const Native=window.EventSource;window.streamProbe=[];
  window.EventSource=class extends Native{
   constructor(...args){super(...args);this.frames={state:0,'state-delta':0};this.opens=0;streamProbe.push(this);
    this.addEventListener('open',()=>this.opens++);
    for(const name of Object.keys(this.frames))this.addEventListener(name,()=>this.frames[name]++);
   }
  };
 });
 const identity=await context.request.get(base+'/fixture/assembly').then(response=>response.json());
 assert.equal(identity.serverRevision,record.server.revision);
 assert.equal(identity.serverVersion,record.server.version);
 if(installedPackage){
  assert.equal(identity.installedWheel,true);assert.equal(identity.nodeUnavailable,true);
  assert.equal(identity.installedPackage,resolve(installedPackage));
  assert.equal(identity.staticFilesVerified,Object.keys(manifest.assets).length+1);
  const component=await context.request.get(base+'/client-component.json');
  assert.equal(component.status(),200);
  const bytes=await component.body();
  assert.equal(bytes.length,record.client.component.bytes);
  assert.equal(hash(bytes),record.client.component.sha256);
  assert.deepEqual(JSON.parse(bytes.toString('utf8')),manifest,'Serve the exact installed component receipt');
 }else assert.equal(identity.serverRevision,manifest.pairedServer.revision);
 assert.deepEqual(identity.clientManifestPeer,manifest.pairedServer,'Unqualified shared-input peer is not the actual server identity');
 assert.deepEqual(identity.clientSource,manifest.source);
 for(const name of ['index.html','build.json']){
  const response=await context.request.get(base+(name==='index.html'?'/':'/'+name));assert.equal(response.status(),200);
  assert.equal(hash(await response.body()),manifest.assets[name].sha256,name+' is served from the installed artifact');
 }
 const badToken=await context.request.get(base+'/api/state',{headers:{Authorization:'Bearer deliberately-invalid-fixture-token'}});assert.equal(badToken.status(),401);
 await page.goto(base);await showFixtureConversation(page);
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeVisible();
 const build=await page.evaluate(()=>window.amplifier.getState().client.build);
 assert.deepEqual(build,{serverVersion:identity.serverVersion,frontend:manifest.build});
 const history=async()=>{const response=await context.request.get(base+'/fixture/message-source');assert.equal(response.status(),200);return response.json()};
 const assistantRendered=async count=>{
  await expect(page.getByRole('heading',{name:'Ready',exact:true})).toHaveCount(count);
  const replies=page.getByText('I received your message and attachments.',{exact:true});
  await expect(replies).toHaveCount(count);await expect(replies.last()).toBeVisible();
 };
 const send=async(text,count)=>{
  await composer.fill(text);await page.locator('[data-part="composer-primary-action"]').click();
  await expect.poll(async()=> (await history()).sends.length).toBe(count);
  await expect.poll(async()=> (await history()).sessions.flatMap(row=>row.messages||[]).filter(row=>row.role==='assistant').length).toBe(count);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId).status)).toBe('idle');
  await expect(page.getByText(text,{exact:true})).toBeVisible();
  await assistantRendered(count);
 };
 await send('History through the paired server',1);
 await page.reload();await showFixtureConversation(page);
 await expect(page.getByText('History through the paired server',{exact:true})).toBeVisible();
 await assistantRendered(1);
 await send('A second accepted message',2);
 await composer.fill('Unfinished paired-server draft');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('Unfinished paired-server draft');
 const before=await history(),transport=await page.evaluate(()=>({
  opens:streamProbe.reduce((n,source)=>n+source.opens,0),baselines:streamProbe.reduce((n,source)=>n+source.frames.state,0)}));
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const disconnected=await context.request.post(base+'/fixture/disconnect',{data:{clientId}});
 assert.equal(disconnected.status(),200);assert.equal((await disconnected.json()).disconnected,true);
 // Native EventSource retry reuses its JS object. Observe the actual reopened
 // connection and fresh baseline, not allocation of another wrapper object.
 await expect.poll(()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.opens,0)),{timeout:15000}).toBeGreaterThan(transport.opens);
 await expect.poll(()=>page.evaluate(()=>streamProbe.reduce((n,source)=>n+source.frames.state,0)),{timeout:15000}).toBeGreaterThan(transport.baselines);
 await expect(composer).toHaveText('Unfinished paired-server draft');
 const deltas=await page.evaluate(()=>streamProbe.at(-1).frames['state-delta']);
 const response=await context.request.post(base+'/api/actions',{headers:{'X-Amplifier-Client':clientId},data:{action:'view.update',args:{patch:{navWidth:417}}}});
 assert.equal(response.status(),200);assert.equal((await response.json()).accepted,true);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.navWidth)).toBe(417);
 await expect.poll(()=>page.evaluate(()=>streamProbe.at(-1).frames['state-delta'])).toBeGreaterThan(deltas);
 await page.reload();await showFixtureConversation(page);await expect(composer).toHaveText('Unfinished paired-server draft');
 await assistantRendered(2);
 assert.notEqual(await page.evaluate(()=>window.amplifier.getState().client.id),clientId,'Reload creates a new writable client identity');
 assert.deepEqual(await history(),before,'Reconnect/reload preserve canonical history and do not execute work again');
 assert.equal(sends.length,2);assert.equal(new Set(sends.map(row=>row.id)).size,2);
 const legacyNotice=await qualifyLegacyNotice({page,composer,history,actions,privateRequests,screenshot});
 assert.equal(sends.length,2,'Notice interactions did not add a send');
 assert.deepEqual(errors,[]);
 result={passed:true,qualification,identity,build,
  authRejectsMissingAndInvalidCredential:true,sseSelectedByFixture:true,realSSEDeltaObserved:true,
  sends:2,distinctAcceptedCommandIds:2,historyDigest:hash(JSON.stringify(before.sessions)),historyPreserved:true,
  assistantRenderedAfterSendAndReload:true,draftSurvivedReconnectAndReload:true,legacyNotice,screenshots,
  limits:['Synthetic runtime/providers, not native execution or model calls',
   installedPackage?'Node-free Python host; Node remains the browser test driver. Server revision is externally declared and bound to installed Python hashes; dependencies are not requalified.':
    'Source server composition, not an unmodified published wheel',
   'The component manifest peer is unqualified shared-input provenance, not the actual server revision',
   'No hardware, live adoption, cross-version or AHP qualification']};
}catch(error){
 failure=error;
 if(page)try{await screenshot('failure')}catch{}
 result={passed:false,qualification,screenshots,failure:String(error.stack||error)};
 throw error;
}finally{
 try{
  await finishFixture({label:'Paired web server',close:[['Paired web browser',()=>browser?.close()]],stop,originalError:failure});
  if(result?.passed)result.bytesVerifiedAfterShutdown=true;
 }
 catch(error){
  failure??=error;
  result={...result,passed:false,cleanupFailed:true};
  throw error;
 }finally{
  if(result){
   try{await writeFile(join(artifact,'browser-result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result))}
   catch(error){if(failure)console.error('Paired browser evidence write failed:',error);else throw error}
  }
 }
}
