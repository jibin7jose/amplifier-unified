// Integration: actual source server + installed independent web artifact.
// Only runtime/providers are synthetic. Fixture selection forces the real SSE
// path; server build/version metadata, auth, actions and history are untouched.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {startFixture} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const [serverRoot,recordPath,stateParent]=process.argv.slice(2);
if(!serverRoot||!recordPath||!stateParent||!process.env.AMPLIFIER_TEST_PYTHON)throw Error('Supply server root, assembly record, state parent and AMPLIFIER_TEST_PYTHON');
const record=JSON.parse(await readFile(recordPath)),manifest=record.client.manifest;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const {base,stop}=await startFixture({command:process.env.AMPLIFIER_TEST_PYTHON,
 args:[fileURLToPath(new URL('../../tests/fixtures/client_paired_server.py',import.meta.url)),
  '--server-root',serverRoot,'--assembly-record',recordPath,'--state-parent',stateParent],
 env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},tag:'client-paired-server',name:'Paired web server'});
let browser,failure;
try{
 const unauthenticated=await fetch(base+'/api/state');assert.equal(unauthenticated.status,401);
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[],sends=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){
  const body=request.postDataJSON();if(body.action==='conversation.send')sends.push(body);
 }});
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
 assert.equal(identity.serverRevision,manifest.pairedServer.revision);
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
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,qualification:'actual-source-server-with-installed-independent-web-artifact',identity,build,
  authRejectsMissingAndInvalidCredential:true,sseSelectedByFixture:true,realSSEDeltaObserved:true,
  sends:2,distinctAcceptedCommandIds:2,historyDigest:hash(JSON.stringify(before.sessions)),historyPreserved:true,
  assistantRenderedAfterSendAndReload:true,draftSurvivedReconnectAndReload:true,limits:['Synthetic runtime/providers, not native execution or model calls','Source server composition, not an unmodified published wheel','No hardware, live adoption, cross-version or AHP qualification']}));
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Paired web server',close:[['Paired web browser',()=>browser?.close()]],stop,originalError:failure})}
