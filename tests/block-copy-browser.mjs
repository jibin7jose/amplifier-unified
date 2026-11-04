// Run only in the manager's existing DTU, after building current candidate assets.
// Uses production UI + existing synthetic host fixture; no model/provider calls.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import assert from 'node:assert/strict';
import {readComposerDraft} from '../frontend/tests/composer-test-helpers.mjs';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const root=fileURLToPath(new URL('../',import.meta.url));
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||await mkdtemp(join(tmpdir(),'unified-block-copy-'));
await mkdir(artifacts,{recursive:true});
const tracePath=join(artifacts,'unified-block-copy-trace.zip');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--canvas-versions'],{stdio:['ignore','pipe','inherit']});
let browser,context,page,stage='startup';
const evidence={status:'running',copies:[],reference:null};
const save=()=>writeFile(join(artifacts,'block-copy-result.json'),JSON.stringify({...evidence,stage},null,2));
async function bounded(promise,label){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(`${label} did not settle within 30 seconds`)),30000)})])}finally{clearTimeout(timer)}}
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 context=await browser.newContext({viewport:{width:1500,height:1050},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 context.setDefaultTimeout(30000);context.setDefaultNavigationTimeout(30000);
 await context.tracing.start({screenshots:true,snapshots:true});
 page=await context.newPage();const errors=[];
 page.on('pageerror',error=>errors.push({name:error.name,message:error.message,stack:error.stack,topURL:page.url()}));
 const exceptionSession=await context.newCDPSession(page),exceptionContexts=new Map(),exceptions=[];
 exceptionSession.on('Runtime.executionContextCreated',({context})=>exceptionContexts.set(context.id,context.auxData));
 exceptionSession.on('Runtime.exceptionThrown',({exceptionDetails})=>exceptions.push({
  ...exceptionDetails,frame:exceptionContexts.get(exceptionDetails.executionContextId)}));
 await exceptionSession.send('Runtime.enable');
 await page.addInitScript(()=>{
  const Native=window.EventSource,Socket=window.WebSocket;
  window.WebSocket=class extends Socket{constructor(...args){super(...args);this.addEventListener('message',event=>{if(JSON.parse(event.data).event==='state')window.blockCopyBaselineReceived=true})}};
  window.blockCopyBaselineReceived=false;
  window.EventSource=class extends Native{
   constructor(...args){super(...args);this.addEventListener('state',()=>{window.blockCopyBaselineReceived=true})}
  };
 });
 const action=(name,args={})=>bounded(page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]),`App action ${name}`);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const sent=async()=>(await (await page.request.get(url+'/fixture')).json()).sent;
 const clipboard=()=>bounded(page.evaluate(()=>navigator.clipboard.readText()),'Read clipboard');
 let copySequence=0;
 async function seedClipboard(label){
  const sentinel=`block-copy sentinel ${++copySequence}: ${label}`;
  await page.evaluate(text=>navigator.clipboard.writeText(text),sentinel);
  await expect.poll(clipboard).toBe(sentinel);
  return sentinel;
 }
 const canvas=page.locator('.a-canvas-viewer');
 async function ready({home=false}={}){
  // Current startup is stream-first. Do not send actions against an empty baseline.
  await page.waitForFunction(()=>window.blockCopyBaselineReceived&&Array.isArray(window.amplifier?.getState()?.sessions));
  if(home)await expect(page.locator('.a-adaptive-home')).toBeVisible();
  else await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 }
 async function copy(button,expected){
  stage='copy '+await button.getAttribute('aria-label');await save();
  const sentinel=await seedClipboard(stage);assert.notEqual(sentinel,expected);
  await button.focus();await page.keyboard.press('Enter');
  await expect.poll(clipboard).toBe(expected);
  await expect(button.locator('..').getByRole('status')).toHaveText('Copied.');
  evidence.copies.push({label:stage,sentinel,expected,completed:true});await save();
 }
 await page.goto(url);await ready({home:true});
 await action('session.create');
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const raw='\t  α😀\r\n\r\n  tail\t\r\n\r\n';
 const nested='1.\tOuter\r\n\r\n\t- Inner\r\n\r\n\t  ```txt\r\n\t  \tα😀\r\n\t  \r\n\t    tail\t\r\n\t  \r\n\t  ```\r\n';
 const table='| Left | Right |\r\n| :--- | ---: |\r\n| a\\|b | **α😀** |\r\n';
 const listedTable='- Table\r\n\r\n'+table.split('\r\n').filter(Boolean).map(line=>'  '+line+'\r\n').join('');
 const message='Neighbor before\r\n\r\n```js\r\n'+raw+'```\r\n\r\n> **outer**\r\n> > `inner`\r\n\r\n'+nested+'\r\n- Quote\r\n\r\n  > **list quote**\r\n\r\n'+listedTable+'\r\nNeighbor after `inline`\r\n\r\n:::writing{variant="document" id="12345"}\nReusable writing\n:::';
 await action('conversation.send',{text:message});
 const assistant=page.locator('.a-assistant').last();
 await expect(assistant.getByRole('button',{name:'Copy code block',exact:true})).toHaveCount(2);
 await expect(assistant.getByRole('button',{name:'Copy quote block',exact:true})).toHaveCount(3);
 await expect(assistant.getByRole('button',{name:'Copy Markdown source',exact:true})).toHaveCount(1);
 const firstCopy=assistant.getByRole('button',{name:'Copy code block',exact:true}).first();
 const firstFrame=firstCopy.locator('../..');
 await page.mouse.move(0,0);
 await expect(firstCopy.locator('..')).toHaveCSS('opacity','0');
 const before=await firstFrame.boundingBox();
 await firstFrame.hover();
 await expect(firstCopy.locator('..')).toHaveCSS('opacity','1');
 const buttonBox=await firstCopy.boundingBox(),frameBox=await firstFrame.boundingBox();
 assert.ok(buttonBox.x>=frameBox.x&&buttonBox.x+buttonBox.width<=frameBox.x+frameBox.width+1);
 assert.ok(buttonBox.y>=frameBox.y&&buttonBox.y+buttonBox.height<=frameBox.y+frameBox.height+1);
 await page.mouse.move(0,0);await firstCopy.focus();
 await expect(firstCopy.locator('..')).toHaveCSS('opacity','1');
 await copy(assistant.getByRole('button',{name:'Copy code block',exact:true}).nth(0),raw);
 assert.equal((await firstFrame.boundingBox()).height,before.height,'Copy feedback does not shift content');
 if(process.env.COPY_UI_SCREENSHOT)await page.screenshot({path:process.env.COPY_UI_SCREENSHOT});
 await copy(assistant.getByRole('button',{name:'Copy code block',exact:true}).nth(1),'\tα😀\r\n\r\n  tail\t\r\n\r\n');
 await copy(assistant.getByRole('button',{name:'Copy quote block',exact:true}).nth(1),'`inner`\r\n');
 await copy(assistant.getByRole('button',{name:'Copy quote block',exact:true}).nth(2),'**list quote**\r\n');
 await copy(assistant.getByRole('button',{name:'Copy Markdown source',exact:true}),table);
 // No block toolbar can include a neighboring paragraph or whole-message text.
 await expect(assistant.locator('[data-copy-block]')).toHaveCount(6);
 // Existing whole-message and writing controls must still use their own payloads.
 await seedClipboard('whole message');
 await assistant.getByRole('button',{name:'Copy message as Markdown',exact:true}).click();
 await expect.poll(clipboard).toBe(message);
 await seedClipboard('writing');
 await assistant.getByRole('button',{name:'Copy writing',exact:true}).click();
 await expect.poll(clipboard).toBe('Reusable writing');
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 const markdown='# Canvas\n\n```txt\r\n'+raw+'```\r\n\r\n```mermaid\nflowchart LR\n A-->B\n```\n\n```dot\ndigraph { a -> b }\n```\n\n> **quoted**\r\n\r\n'+table;
 const artifact=(await action('canvas.show',{kind:'markdown',title:'Block copy fixture',content:markdown})).result;
 await expect(canvas.getByRole('button',{name:'Copy code block',exact:true})).toHaveCount(3);
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(0),raw);
 await page.locator('.a-diagram-stage img').nth(1).waitFor({timeout:30000});
 assert.equal(await page.locator('.a-diagram-stage img').count(),2);
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(1),'flowchart LR\n A-->B\n');
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(2),'digraph { a -> b }\n');
 await copy(canvas.getByRole('button',{name:'Copy quote block',exact:true}),'**quoted**\r\n');
 await expect(canvas.getByRole('button',{name:'Copy Markdown source',exact:true})).toHaveCount(1);
 await copy(canvas.getByRole('button',{name:'Copy Markdown source',exact:true}),table);
 // Existing source-span mapping still supports references without toolbar text.
 const sentBeforeReference=await sent();
 const span=canvas.locator('blockquote [data-canvas-source-start]').first();
 await span.evaluate(element=>{const range=document.createRange();range.selectNodeContents(element);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'))});
 await expect(page.getByRole('button',{name:'Reference in chat',exact:true})).toBeEnabled();
 stage='reference action';
 const referenceResponse=page.waitForResponse(response=>{
  if(!response.url().endsWith('/api/actions'))return false;
  try{const body=response.request().postDataJSON();return body?.action==='canvas.views.command'&&body.args?.action==='canvas.reference'}catch{return false}
 });
 await page.getByRole('button',{name:'Reference in chat',exact:true}).click();
 const response=await referenceResponse;assert.equal(response.status(),200);
 const reference=(await response.json()).result;
 assert.equal(reference.sent,false);assert.equal(reference.version,1);assert.equal(reference.excerpt,'quoted');
 assert.equal(reference.id,artifact.id);
 const uri=new URL(reference.reference);
 assert.equal(uri.protocol,'amplifier-canvas:');assert.equal(uri.hostname,'artifact');
 assert.equal(uri.pathname,'/'+artifact.id);assert.equal(uri.searchParams.get('version'),'1');
 assert.equal(uri.searchParams.get('session'),reference.sessionId);
 const expectedReference=`From [Block copy fixture · Version 1](${reference.reference}):\n\n> quoted\n\n`;
 assert.equal(reference.draft,expectedReference);
 const referenceComposer=page.getByRole('textbox',{name:'Message Amplifier'});
 async function exactReference(){
  await expect.poll(()=>readComposerDraft(referenceComposer)).toBe(expectedReference);
  await expect(referenceComposer.locator('.a-composer-markdown-source')).toBeVisible();
  assert.equal(await referenceComposer.textContent(),expectedReference,'visible editor retains URI, version and complete excerpt');
  await expect.poll(async()=>(await savedDraft()).draft).toBe(expectedReference);
  const persisted={view:await savedDraft()};
  return {text:await referenceComposer.textContent(),draft:persisted.view.draft,document:persisted.view.draftDocument};
 }
 async function savedDraft(){
  const current=await state();
  return (await (await page.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':current.client.id}})).json()).view;
 }
 stage='reference before reload';
 evidence.reference={uri:reference.reference,version:reference.version,excerpt:reference.excerpt,expected:expectedReference,before:await exactReference()};await save();
 if(process.env.BLOCK_COPY_OLD_ORACLE==='1'){
  // Deliberately retain the obsolete assertion as a discriminating control.
  await expect(referenceComposer.locator('blockquote')).toContainText('quoted',{timeout:1000});
  throw Error('OLD_ORACLE_UNEXPECTEDLY_PASSED');
 }
 await page.reload();await ready();
 stage='reference after reload';evidence.reference.after=await exactReference();await save();
 await referenceComposer.press('Control+End');await referenceComposer.pressSequentially('Temporary edit');
 await expect.poll(()=>readComposerDraft(referenceComposer)).toBe(expectedReference+'Temporary edit');
 await expect.poll(async()=>(await savedDraft()).draft).toBe(expectedReference+'Temporary edit');
 await referenceComposer.press('Control+z');await exactReference();
 assert.deepEqual(await sent(),sentBeforeReference,'reference/edit/undo must not send');
 stage='reference Send';
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(()=>readComposerDraft(referenceComposer)).toBe('');
 await expect.poll(async()=>(await sent()).length).toBe(sentBeforeReference.length+1);
 const sentReference=(await sent()).at(-1);
 assert.equal(sentReference.text,expectedReference);
 assert.equal(sentReference.sessionId,reference.sessionId);
 evidence.reference.sent=sentReference;await save();
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(0),raw);
 const saved=(await action('canvas.versions.inspect',{id:artifact.id,version:1,includeSource:true})).result;
 assert.equal(saved.source.content,markdown,'Copy and reload must not change saved content');
 await page.getByRole('button',{name:'Source',exact:true}).click();
 // Await the source view itself: before its response arrives the old toolbar
 // also has one Copy button, but it is a different command/control.
 await expect(canvas.locator('.a-canvas-preview > .a-canvas-code')).toBeVisible();
 await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(1);
 await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),markdown);
 // The rendered source has one block-copy control; the artifact header owns
 // its separate Copy action.
 for(const kind of ['code','text']){
  await action('canvas.show',{kind,title:kind+' fixture',content:raw});
  await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(1);
  await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),raw);
 }
 for(const [kind,content] of [['mermaid','flowchart LR\r\n A-->B\r\n'],['dot','digraph { a -> b }\r\n']]){
  await action('canvas.show',{kind,content});
  await canvas.locator('.a-diagram-stage img').waitFor({timeout:30000});
  await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),content);
 }
 await action('canvas.show',{kind:'json',content:'not JSON'});
 const parseError=await page.evaluate(()=>{try{JSON.parse('not JSON')}catch(error){return error.message}});
 await copy(canvas.getByRole('button',{name:'Copy preview error',exact:true}),parseError);
 const records=[{name:'α',nested:{tab:'\t',emoji:'😀'}},{name:'neighbor'}];
 await action('canvas.show',{kind:'json',content:JSON.stringify(records)});
 await copy(canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true}),JSON.stringify(records[0],null,2));
 await canvas.getByRole('searchbox',{name:'Filter data records'}).fill('neighbor');
 await expect(canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true})).toHaveCount(0);
 await copy(canvas.getByRole('button',{name:'Copy record 2 as JSON',exact:true}),JSON.stringify(records[1],null,2));
 await action('canvas.show',{kind:'jsonl',content:records.map(row=>JSON.stringify(row)).join('\r\n')});
 await copy(canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true}),JSON.stringify(records[0],null,2));
 // Real browser permission denial, not a mock success.
 const deniedSentinel=await seedClipboard('permission denied');await context.clearPermissions();
 const cdp=await context.newCDPSession(page);
 const {targetInfo}=await cdp.send('Target.getTargetInfo');
 for(const allowWithoutSanitization of [false,true])
  await cdp.send('Browser.setPermission',{permission:{name:'clipboard-write',allowWithoutSanitization},setting:'denied',origin:new URL(url).origin,browserContextId:targetInfo.browserContextId});
 const denied=canvas.getByRole('button',{name:'Copy record 1 as JSON',exact:true});
 await denied.locator('../..').hover();await denied.click();await expect(denied.locator('..').getByRole('status')).toContainText('denied');
 await cdp.send('Browser.resetPermissions',{browserContextId:targetInfo.browserContextId});
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 await expect.poll(clipboard).toBe(deniedSentinel);
 // Unsupported API is a separate branch: only this branch is deliberately stubbed.
 const unavailableSentinel=await seedClipboard('unavailable');
 await page.evaluate(()=>{window.originalClipboard=Object.getOwnPropertyDescriptor(navigator,'clipboard');Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})});
 await denied.locator('../..').hover();await denied.click();await expect(denied.locator('..').getByRole('status')).toContainText('unavailable');
 await page.evaluate(()=>{if(window.originalClipboard)Object.defineProperty(navigator,'clipboard',window.originalClipboard);else delete navigator.clipboard});
 await expect.poll(clipboard).toBe(unavailableSentinel);
 // Stored source is fetched for the selected version before exposing Copy.
 // Use inert HTML only to exercise the host's existing externalization path.
 const savedSource='<p>Saved source α😀</p>\r\n<!--'+'x'.repeat(1000100)+'-->\r\n';
 // The inline 1 MB cap stays enforced; the supported owned-file path admits
 // this larger payload without raising the cap or manufacturing storage.
 await assert.rejects(()=>action('canvas.show',{kind:'html',title:'Oversized inline rejection',content:savedSource}));
 const fixtureState=await (await page.request.get(url+'/fixture')).json();
 assert.equal(typeof fixtureState.storedCopyPath,'string');
 const storedArtifact=(await action('canvas.show',{kind:'html',title:'Stored copy fixture',path:fixtureState.storedCopyPath})).result;
 await expect.poll(async()=>(await state()).canvas.contentResource?.$resource).toBeTruthy();
 let heldSource;
 const requested=new Promise(resolve=>heldSource=resolve);
 await page.route('**/api/canvas/*/source?version=*',route=>heldSource(route));
 await page.getByRole('button',{name:'Source',exact:true}).click();
 let sourceTimer;
 const sourceRoute=await Promise.race([requested,new Promise((_,reject)=>{sourceTimer=setTimeout(()=>reject(Error('Selected saved-source request did not arrive')),15000)})]).finally(()=>clearTimeout(sourceTimer));
 await expect(canvas.getByText('Loading saved source…',{exact:true})).toBeVisible();
 await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(0);
 await sourceRoute.continue();
 await page.unroute('**/api/canvas/*/source?version=*');
 await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),savedSource);
 assert.equal((await action('canvas.versions.inspect',{id:storedArtifact.id,version:1,includeSource:true})).result.source.content,savedSource);
 // Hold consumer completion after the real v1 body arrives. Holding the network
 // route instead only proves abort-on-remount, not rejection of a late success.
 await page.getByRole('button',{name:'Preview',exact:true}).click();
 await expect(canvas.locator('.a-canvas-preview > .a-canvas-code')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Preview',exact:true})).toHaveAttribute('aria-pressed','true');
 stage='successful late saved-source completion';await save();
 const expectedOldBody={id:storedArtifact.id,bytes:Buffer.byteLength(savedSource),
  sha256:createHash('sha256').update(savedSource).digest('hex')};
 await page.evaluate(expected=>{
  const descriptor=Object.getOwnPropertyDescriptor(Response.prototype,'text'),nativeText=descriptor.value;
  const selected=()=>{const canvas=window.amplifier.getState().canvas;return {id:canvas.id,version:canvas.selectedVersion??canvas.revision}};
  let release;
  const gate=new Promise(resolve=>{release=resolve});
  const control=window.blockCopyLateSource={facts:{expected,phase:'armed',events:[]},release,
   record:event=>{
    const events=control.facts.events;
    if(['held','v2observed','released','fulfilled','observedafter'][events.length]!==event)
     throw Error('Unexpected late-source event order');
    events.push({sequence:events.length+1,event});
   },
   restore:()=>Object.defineProperty(Response.prototype,'text',descriptor)};
  Object.defineProperty(Response.prototype,'text',{...descriptor,value:function(...args){
   const address=new URL(this.url,location.href);
   if(address.origin!==location.origin||address.pathname!==`/api/canvas/${expected.id}/source`||address.searchParams.get('version')!=='1')
    return nativeText.apply(this,args);
   control.restore(); // One response only; all later reads use the native method.
   Object.assign(control.facts,{phase:'reading',status:this.status,path:address.pathname,version:1});
   control.pending=nativeText.apply(this,args).then(async text=>{
    const bytes=new TextEncoder().encode(text);
    Object.assign(control.facts,{bodyArrivedAt:performance.now(),bodyUsed:this.bodyUsed,
     bytes:bytes.length,arrivalSelection:selected(),
     sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('')});
    if(this.status!==200||!this.bodyUsed||bytes.length!==expected.bytes||control.facts.sha256!==expected.sha256)
     throw Error('The delayed consumer must receive the exact real successful v1 body');
    control.record('held');
    Object.assign(control.facts,{phase:'held',verifiedAt:performance.now()});
    await gate;
    return text; // The actual native body, not a substituted fixture constant.
   }).then(text=>{
    control.record('fulfilled');
    Object.assign(control.facts,{phase:'fulfilled',fulfilledAt:performance.now(),completionSelection:selected()});
    return text;
   }).catch(error=>{Object.assign(control.facts,{phase:'failed',error:error.message});throw error});
   return control.pending;
  }});
 },expectedOldBody);
 try{
 await page.getByRole('button',{name:'Source',exact:true}).click();
 await expect.poll(async()=>{
  const facts=await page.evaluate(()=>window.blockCopyLateSource.facts);
  assert.notEqual(facts.phase,'failed',JSON.stringify(facts));
  return facts.phase;
 }).toBe('held');
 evidence.lateSource=await page.evaluate(()=>window.blockCopyLateSource.facts);await save();
 assert.deepEqual(evidence.lateSource.arrivalSelection,{id:storedArtifact.id,version:1});
 await expect(canvas.getByText('Loading saved source…',{exact:true})).toBeVisible();
 await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(0);
 const newStoredSource='<p>New selected stored-source version</p>\n';
 await action('canvas.versions.revise',{id:storedArtifact.id,expectedRevision:1,content:newStoredSource});
 // Even inert saved HTML is an executable preview. Use the fixture's explicit
 // version/reset controls while the old body's consumer completion remains held.
 await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('2');
 await page.getByRole('group',{name:'Confirm saved preview version',exact:true}).getByRole('button',{name:'Load v2 — resets preview and runs its code',exact:true}).click();
 await expect.poll(async()=>(await state()).canvas.selectedVersion).toBe(2);
 await action('canvas.view',{id:storedArtifact.id,patch:{source:true}});
 await expect.poll(async()=>(await state()).canvas.content).toBe(newStoredSource);
 await expect(canvas.getByText('Loading saved source…',{exact:true})).toHaveCount(0);
 await expect.poll(()=>canvas.locator('.a-canvas-preview > .a-canvas-code').textContent()).toBe(newStoredSource);
 await expect(canvas.locator('.a-block-copy-notice')).toHaveText('');
 // Preserve the normal copy count, and prove existing feedback/clipboard survive
 // the old fulfillment, not only that a later click can copy the new source.
 await copy(canvas.getByRole('button',{name:'Copy canvas source',exact:true}),newStoredSource);
 evidence.lateSource=await bounded(page.evaluate(async id=>{
  const control=window.blockCopyLateSource,current=window.amplifier.getState().canvas;
  if(control.facts.phase!=='held'||current.id!==id||current.selectedVersion!==2)
   throw Error('Release requires the real held v1 body and confirmed v2 selection');
  control.record('v2observed');
  Object.assign(control.facts,{selectionObservedAt:performance.now(),releaseSelection:{id:current.id,version:current.selectedVersion}});
  control.record('released');
  control.facts.releasedAt=performance.now();control.release();
  await control.pending;
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  control.record('observedafter');
  control.facts.observedAfterFulfillmentAt=performance.now();
  return control.facts;
 },storedArtifact.id),'Release successful old-source completion');
 await save();
 const late=evidence.lateSource;
 assert.equal(late.phase,'fulfilled');assert.equal(late.status,200);assert.equal(late.bodyUsed,true);
 assert.equal(late.bytes,expectedOldBody.bytes);assert.equal(late.sha256,expectedOldBody.sha256);
 // Clock precision may tie correctly ordered microtasks; guarded events prove causality.
 assert.deepEqual(late.events,['held','v2observed','released','fulfilled','observedafter'].map((event,index)=>({sequence:index+1,event})));
 assert.ok(late.bodyArrivedAt<=late.verifiedAt&&late.verifiedAt<=late.selectionObservedAt&&late.selectionObservedAt<=late.releasedAt&&late.releasedAt<=late.fulfilledAt&&late.fulfilledAt<=late.observedAfterFulfillmentAt,JSON.stringify(late));
 assert.deepEqual(late.completionSelection,{id:storedArtifact.id,version:2});
 assert.equal((await state()).canvas.selectedVersion,2);
 assert.equal(await canvas.locator('.a-canvas-preview > .a-canvas-code').textContent(),newStoredSource);
 await expect(canvas.locator('.a-block-copy-notice')).toHaveText('Copied.');
 assert.equal(await clipboard(),newStoredSource);
 late.after={selectedVersion:2,displayedSha256:createHash('sha256').update(await canvas.locator('.a-canvas-preview > .a-canvas-code').textContent()).digest('hex'),
  clipboardSha256:createHash('sha256').update(await clipboard()).digest('hex'),feedback:await canvas.locator('.a-block-copy-notice').textContent()};
 assert.equal((await action('canvas.versions.inspect',{id:storedArtifact.id,version:1,includeSource:true})).result.source.content,savedSource);
 late.storedV1Unchanged=true;await save();
 }finally{
  const observed=await page.evaluate(()=>{const control=window.blockCopyLateSource;control?.restore();return control?.facts}).catch(()=>null);
  evidence.lateSource={...evidence.lateSource,...observed};
  await save();
 }
 await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('1');
 await page.getByRole('group',{name:'Confirm saved preview version',exact:true}).getByRole('button',{name:'Load v1 — resets preview and runs its code',exact:true}).click();
 await expect.poll(async()=>(await state()).canvas.selectedVersion).toBe(1);
 // A failed saved-source read must not enable copying an empty/partial preview.
 await page.evaluate(()=>navigator.clipboard.writeText('retained on source failure'));
 await page.getByRole('button',{name:'Preview',exact:true}).click();
 await page.route('**/api/canvas/*/source?version=*',route=>route.fulfill({status:503,body:'Unavailable'}));
 await page.getByRole('button',{name:'Source',exact:true}).click();
 await expect(canvas.getByRole('alert')).toHaveText('The saved source could not be loaded.');
 await expect(canvas.getByRole('button',{name:'Copy canvas source',exact:true})).toHaveCount(0);
 await expect.poll(clipboard).toBe('retained on source failure');
 await page.unroute('**/api/canvas/*/source?version=*');
 // Hold completion, not invocation, to exercise rapid clicks/version replacement.
 await action('canvas.select',{id:artifact.id,version:1});
 await action('canvas.view',{id:artifact.id,patch:{source:false}});
 await seedClipboard('held completion');
 await page.evaluate(()=>{
  const write=navigator.clipboard.writeText.bind(navigator.clipboard);window.copyCalls=[];
  Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:text=>{
   window.copyCalls.push(text);const completion=write(text);
   return new Promise((resolve,reject)=>{window.finishCopy=()=>completion.then(resolve,reject)});
  }});
 });
 const oldButton=canvas.getByRole('button',{name:'Copy code block',exact:true}).nth(0);
 await oldButton.evaluate(button=>{button.click();button.click()});
 await expect(oldButton.locator('..').getByRole('status')).toHaveText('Copying…');
 assert.deepEqual(await page.evaluate(()=>window.copyCalls),[raw]);
 await action('canvas.versions.revise',{id:artifact.id,expectedRevision:1,content:'```txt\nnew version\n```'});
 await action('canvas.select',{id:artifact.id,version:2});
 await page.waitForFunction(()=>document.querySelector('.a-canvas-preview')?.textContent.includes('new version'));
 await page.evaluate(()=>window.finishCopy());
 await expect.poll(clipboard).toBe(raw);
 assert.deepEqual(await page.evaluate(()=>window.copyCalls),[raw],'No clipboard dispatch after await may retarget a newer version');
 await expect(canvas.locator('.a-block-copy-notice')).toHaveText('');
 await page.reload();await ready();
 await copy(canvas.getByRole('button',{name:'Copy code block',exact:true}),'new version\n');
 await page.setViewportSize({width:390,height:844});
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await action('view.update',{patch:{canvasFocused:true}});
 // Details was opened for the source controls above. On narrow Canvas it is
 // intentionally a covering sheet with an inert reader, not a missing Copy.
 await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
 await expect(page.getByRole('region',{name:'Canvas details',exact:true})).toBeHidden();
 await expect(page.locator('.a-canvas-reader')).not.toHaveAttribute('inert','');
 const touch=canvas.getByRole('button',{name:'Copy code block',exact:true});
 stage='narrow touch visibility';await save();
 await expect(touch).toBeVisible();
 const size=await touch.boundingBox();assert.ok(size.width>=44&&size.height>=44);
 await expect(touch.locator('..')).toHaveCSS('opacity','1');
 const touchSentinel=await seedClipboard('narrow touch');
 stage='narrow touch clipboard';await save();
 await page.screenshot({path:join(artifacts,'narrow-before-touch.png')});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:size.x+size.width/2,y:size.y+size.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await expect.poll(clipboard).toBe('new version\n');
 await expect(touch.locator('..').getByRole('status')).toHaveText('Copied.');
 evidence.copies.push({label:'narrow touch',sentinel:touchSentinel,expected:'new version\n',completed:true});
 await page.screenshot({path:join(artifacts,'narrow-after-touch.png')});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 console.log(JSON.stringify({pageErrors:errors,frameExceptions:exceptions}));
 assert.deepEqual(errors,[]);
 evidence.status='passed';stage='complete';await save();
 console.log('PASS block-copy browser: SSE-first baseline, exact source clipboard, assistant/Canvas list/quote/table blocks, diagrams, code/text/JSON, saved-source readiness/failure, reference spans, reload, permission denial, unsupported API, pending/version race, keyboard/touch, writing/message regression.');
}catch(error){
 evidence.status='failed';evidence.error=String(error);
 if(page){
  evidence.diagnostic=await page.evaluate(()=>({width:innerWidth,height:innerHeight,focused:document.activeElement?.outerHTML.slice(0,500),canvas:window.amplifier?.getState()?.canvas,buttons:[...document.querySelectorAll('.a-canvas-viewer button')].map(el=>{const r=el.getBoundingClientRect();return {label:el.getAttribute('aria-label'),rect:{x:r.x,y:r.y,width:r.width,height:r.height},opacity:getComputedStyle(el.parentElement).opacity,display:getComputedStyle(el).display}})})).catch(()=>null);
  await page.screenshot({path:join(artifacts,'failure.png')}).catch(()=>{});
 }
 await save();throw error;
}finally{try{await context?.tracing.stop({path:tracePath});if(context)console.log(JSON.stringify({tracePath}))}finally{await browser?.close();fixture.kill()}}
