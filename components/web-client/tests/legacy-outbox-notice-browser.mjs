// Real Vite UI, synthetic HTTP/SSE only. Each case owns a fresh browser context.
// No runtime/provider or user browser data. Pending scoped delivery is separately
// exercised by delivery-reconciliation-browser.mjs, not reimplemented here.
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/legacy-outbox-notice';
const payloadKey='amplifier.messageOutbox.v1',metadataKey='amplifier.legacyOutboxNotice.v1';
const noticeText='This tab retains earlier message copies with no verified host identity. Their delivery is unknown. Nothing was sent again.';
const storageText='The notice preference could not be saved. It may reappear after reload. Message copies are unchanged.';
const draft='Keep this next unsent thought';
const seed=[
 {id:'unbound-unknown',commandId:'unbound-input-one',sessionId:'chat',createdAt:123,
  text:'legacy-private-unknown-body <img src="/api/legacy-private-image">',
  status:'unknown',error:'legacy-private-unknown-error',attachmentIds:['legacy-private-attachment'],
  attachments:[{id:'legacy-private-attachment',name:'legacy-private-reference.txt',size:19,
   url:'/api/attachments/legacy-private-attachment',content:'legacy-private-attachment-body'}],
  document:{version:2,segments:[{type:'text',text:'legacy-private-document-body'}]}},
 {id:'unbound-failed',commandId:'unbound-input-two',sessionId:'other',createdAt:124,
  text:'legacy-private-failed-body',status:'failed',error:'legacy-private-failed-error',attachmentIds:[]},
];
const privateMarker='legacy-private-';
// Whitespace is deliberate: parsing/reserializing the journal is a mutation too.
const rawFor=rows=>'\n'+JSON.stringify(rows,null,2)+'\n';
const hiddenFor=rows=>JSON.stringify(rows.map(row=>
 JSON.stringify([row.id,row.commandId,row.sessionId,row.createdAt??null])).sort());
const session=id=>({id,title:id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',
 nativeIdentity:id,status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]});
const freshState=()=>({
 revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:false},
 sessions:[session('chat'),session('other')],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],
 selectedSessionId:'chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],
  providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false},
});
const cases=[],screenshots=[];
let vite,browser,origin;

function installStorageCase({payloadKey,metadataKey,raw,metadata,fault,privateMarker}){
 const storage=sessionStorage,proto=Storage.prototype;
 const get=proto.getItem,set=proto.setItem,remove=proto.removeItem,clear=proto.clear;
 const marker='legacy-notice-fixture-seeded';
 if(get.call(storage,marker)!=='1'){
  set.call(storage,payloadKey,raw);
  if(metadata!==null)set.call(storage,metadataKey,metadata);
  set.call(storage,marker,'1');
 }
 const writes=[],blocked={read:0,write:0};
 let rendered=false;
 proto.getItem=function(key){
  if(this===storage&&key===metadataKey&&fault==='read'){
   blocked.read++;throw new DOMException('Synthetic notice metadata read refusal','SecurityError');
  }
  return get.call(this,key);
 };
 proto.setItem=function(key,value){
  const tracked=this===storage&&(key===payloadKey||key===metadataKey);
  const failed=this===storage&&key===metadataKey&&fault==='write';
  if(tracked)writes.push({operation:'set',key,failed});
  if(failed){blocked.write++;throw new DOMException('Synthetic notice metadata write refusal','QuotaExceededError')}
  return set.call(this,key,value);
 };
 proto.removeItem=function(key){
  if(this===storage&&(key===payloadKey||key===metadataKey))writes.push({operation:'remove',key,failed:false});
  return remove.call(this,key);
 };
 proto.clear=function(){
  if(this===storage)writes.push({operation:'clear',key:payloadKey,failed:false});
  return clear.call(this);
 };
 const inspect=node=>{
  if(node&&((node.textContent||'')+(node.outerHTML||'')).includes(privateMarker))rendered=true;
 };
 // Latch transient exposure as well as checking the settled DOM. Storage is not
 // a rendered surface; the only payload writes here are explicit fixture seeds.
 new MutationObserver(records=>{
  for(const record of records){
   inspect(record.target);
   for(const node of record.addedNodes)inspect(node);
   if(record.oldValue?.includes(privateMarker))rendered=true;
  }
 }).observe(document,{subtree:true,childList:true,characterData:true,characterDataOldValue:true,
  attributes:true,attributeOldValue:true});
 window.legacyNoticeFixture={
  snapshot(){
   inspect(document.body);
   return {raw:get.call(storage,payloadKey),metadata:get.call(storage,metadataKey),
    writes:[...writes],blocked:{...blocked},rendered,
    localPayload:get.call(localStorage,payloadKey),localMetadata:get.call(localStorage,metadataKey)};
  },
  injectPayload(value){set.call(storage,payloadKey,value)},
 };
}

async function runCase(fault){
 const result={fault,passed:false,completed:[],toggles:[],errors:[],blockedRequests:[],
  privateRequests:0,actionNames:[]};
 cases.push(result);
 const state=freshState(),shell=createShellHttpFixture(()=>state),calls=[];
 const drafts=new Map();
 let rows=structuredClone(seed),raw=rawFor(rows);
 const context=await browser.newContext({viewport:{width:1280,height:900},serviceWorkers:'block'});
 const page=await context.newPage();
 page.setDefaultTimeout(8000);page.setDefaultNavigationTimeout(15000);
 page.on('pageerror',error=>result.errors.push(error.message));
 page.on('request',request=>{
  if((request.url()+(request.postData()||'')).includes(privateMarker))result.privateRequests++;
 });
 const notice=page.locator('[data-part="legacy-outbox-notice"]');
 const button=notice.getByRole('button');
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const snapshot=()=>page.evaluate(()=>window.legacyNoticeFixture.snapshot());
 async function screenshot(name){
  const path=`${artifact}/${fault}-${name}.png`;
  await page.screenshot({path});screenshots.push(path);
 }
 async function invariant(){
  const value=await snapshot();
  assert.equal(value.raw,raw,'Retained unknown/failed payload stays byte-for-byte unchanged');
  assert.equal(value.rendered,false,'No retained text, attachment or document is rendered, even transiently');
  assert.equal(value.localPayload,null);assert.equal(value.localMetadata,null);
  assert.deepEqual(value.writes.filter(write=>write.key===payloadKey),[]);
  assert.equal(result.privateRequests,0,'No retained payload is requested or sent in HTTP');
  assert.deepEqual(result.blockedRequests,[]);
  assert.deepEqual(result.errors,[]);
  assert.deepEqual(calls.filter(call=>call.action.startsWith('conversation.')||
   ['message.edit','session.delete','session.archive','attachment.remove','attachment.upload'].includes(call.action)),[],
  'Unbound copies are never adopted, checked, resent, edited or deleted');
  await expect(page.locator('[data-input-id="unbound-input-one"],[data-input-id="unbound-input-two"]')).toHaveCount(0);
 }
 async function presentation(expanded,storageError=false){
  await expect(notice).toBeVisible();
  await expect(button).toHaveCount(1);
  await expect(button).toHaveAttribute('type','button');
  await expect(button).toHaveAttribute('aria-expanded',String(expanded));
  await expect(button).toHaveAccessibleName(expanded?'Hide notice':`Retained copies (${rows.length})`);
  await expect(button).toBeEnabled();
  if(expanded){
   const paragraph=notice.getByRole('status').filter({hasText:noticeText});
   await expect(paragraph).toHaveText(noticeText);await expect(paragraph).toBeVisible();
   assert.equal(await button.getAttribute('aria-controls'),await paragraph.getAttribute('id'));
  }else{
   await expect(notice.getByText(noticeText,{exact:true})).toHaveCount(0);
   assert.equal(await button.getAttribute('aria-controls'),null);
  }
  await expect(notice.getByText(storageText,{exact:true})).toHaveCount(storageError?1:0);
  if(storageError)await expect(notice.getByText(storageText,{exact:true})).toBeVisible();
 }
 async function settled(){
  await expect(composer).toBeEditable();
  // No fixture SSE connection stays on the network. Drain real bootstrap/draft
  // requests before measuring whether a local toggle dispatched ANY action.
  await page.waitForLoadState('networkidle',{timeout:8000});
 }
 async function toggle(expanded,gesture='click'){
  await settled();
  const before=await snapshot(),actionCount=calls.length,serverBefore=JSON.stringify(state);
  const text=state.view.draft||'',handle=await button.elementHandle();
  try{
   if(gesture==='click')await button.click();
   else{await button.focus();await page.keyboard.press(gesture)}
   await presentation(expanded,fault==='write');
   assert.ok(await handle.evaluate(node=>node.isConnected&&
    node===document.querySelector('[data-part="legacy-outbox-notice"] button')),
   'Collapse/reopen retains the same discoverable button');
   if(gesture!=='click')await expect(button).toBeFocused();
   await settled();await expect(composer).toHaveDraft(text);
   assert.equal(calls.length,actionCount,'A notice toggle dispatches no action, including form submission');
   assert.equal(JSON.stringify(state),serverBefore,'A notice toggle does not alter host state');
   const after=await snapshot();
   assert.deepEqual(after.writes.slice(before.writes.length),[
    {operation:'set',key:metadataKey,failed:fault==='write'},
   ],'Only separate notice metadata is written');
   assert.equal(after.metadata,fault==='write'?before.metadata:expanded?'':hiddenFor(rows));
   await invariant();
   result.toggles.push({expanded,gesture,sameButton:true,draftPreserved:true,actionDelta:0,payloadUnchanged:true});
  }finally{await handle?.dispose()}
 }
 async function reload(expanded){
  await invariant();
  await page.reload();await settled();
  await presentation(expanded);await expect(composer).toHaveDraft(state.view.draft||'');
  await invariant();
 }
 async function selectChat(id,expanded){
  // Synthetic peer publishes the selected chat through the production SSE
  // boundary, as in delivery-reconciliation's pending-read remount controls.
  state.selectedSessionId=id;state.view={...state.view,draft:'',draftDocument:null,...drafts.get(id)};
  const revision=++state.revision;
  await page.evaluate(value=>window.emitFixtureState(value),shell.state());
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(id);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().revision)).toBeGreaterThanOrEqual(revision);
  await settled();await presentation(expanded);
  await expect(composer).toHaveDraft(state.view.draft);await invariant();
 }
 async function mobileGeometry(){
  await expect(button).toBeInViewport();await expect(composer).toBeInViewport();
  const geometry=await notice.evaluate(node=>{
   const rect=node.getBoundingClientRect(),control=node.querySelector('button'),box=control.getBoundingClientRect();
   return {width:innerWidth,documentWidth:document.documentElement.scrollWidth,
    noticeWidth:node.clientWidth,noticeScrollWidth:node.scrollWidth,left:rect.left,right:rect.right,
    buttonHit:control.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2))};
  });
  assert.equal(geometry.width,320);assert.ok(geometry.documentWidth<=320);
  assert.ok(geometry.noticeScrollWidth<=geometry.noticeWidth);
  assert.ok(geometry.left>=0&&geometry.right<=320&&geometry.buttonHit,'320px notice and toggle remain usable');
  (result.mobile??=[]).push(geometry);
 }
 try{
  await page.addInitScript(installFixtureEvents);
  await page.addInitScript(installStorageCase,{payloadKey,metadataKey,raw,
   metadata:fault==='read'?hiddenFor(rows):null,fault,privateMarker});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==origin||url.pathname.includes(privateMarker)){
    result.blockedRequests.push(url.origin!==origin?'external-origin':'retained-payload');
    return route.abort();
   }
   if(!url.pathname.startsWith('/api/'))return route.continue();
   const response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:response});
   if(url.pathname==='/api/state')return route.fulfill({json:shell.state()});
   if(url.pathname==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(url.pathname==='/api/outputs/media')return route.fulfill({json:{items:[],overflow:false,mediaRevision:0}});
   if(url.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
   const envelope=request.postDataJSON();
   const body=envelope.action==='shell.command'?(envelope.args.command||envelope.args):envelope;
   calls.push(body);result.actionNames.push(body.action);
   if(body.action==='view.update'){
    const patch=body.args.patch||{};
    if(Object.hasOwn(patch,'draft')){
     const id=body.args.sessionId??state.selectedSessionId;
     drafts.set(id,{draft:patch.draft,draftDocument:patch.draftDocument??null});
    }
    state.view={...state.view,...patch};state.revision++;
   }else if(!['shell.report','attention.read','bundles.list','configuration.defaults','providers.list','theme.list'].includes(body.action)&&
            !(body.action==='runtime.control'&&body.args.operation==='configuration.catalog')){
    result.blockedRequests.push(body.action);
    return route.fulfill({status:409,json:{accepted:false,error:'This fixture refuses unexpected actions.'}});
   }
   return route.fulfill({json:{accepted:true,operationId:body.id,state:shell.state()}});
  });
  await page.goto(origin);await settled();await presentation(true);
  await composer.fill(draft);await expect(composer).toHaveDraft(draft);
  await expect.poll(()=>drafts.get('chat')?.draft).toBe(draft);
  await settled();await invariant();
  result.completed.push('expanded exact notice; unknown and failed copies remain private and byte-identical');
  if(fault==='none'){
   await screenshot('desktop-expanded');
   await toggle(false);await screenshot('desktop-collapsed');
   await reload(false);
   await selectChat('other',false);await selectChat('chat',false);
   await expect(composer).toHaveDraft(draft);
   result.completed.push('hide persists across reload and selected-chat SSE switches; original draft retained');
   await toggle(true,'Enter');
   await selectChat('other',true);await selectChat('chat',true);
   await toggle(false,'Space');await reload(false);
   result.completed.push('same toggle reopens with Enter and hides with Space without submitting the form');
   const oldMetadata=(await snapshot()).metadata;
   rows=[{...rows[0],id:'new-unbound-identity'},rows[1]];raw=rawFor(rows);
   assert.equal(rows.length,seed.length);
   // Fixture-only storage change, not a production edit/delete action. The init
   // script seeds only once, so reload must read these actual replacement bytes.
   await page.evaluate(value=>window.legacyNoticeFixture.injectPayload(value),raw);
   assert.equal((await snapshot()).metadata,oldMetadata);
   await reload(true);
   assert.equal((await snapshot()).metadata,oldMetadata,'Resurfacing does not rewrite the saved preference');
   assert.notEqual(hiddenFor(rows),oldMetadata);
   result.completed.push('same count with one new unbound ID resurfaces after reload; metadata did not change');
   await page.setViewportSize({width:320,height:844});
   await mobileGeometry();await screenshot('mobile-expanded');
   await toggle(false,'Enter');await mobileGeometry();await screenshot('mobile-collapsed');
   await toggle(true,'Space');await mobileGeometry();
   await reload(true);await expect(composer).toHaveDraft(draft);
   result.completed.push('320px expanded/collapsed layout and keyboard toggle; no horizontal overflow');
  }else if(fault==='write'){
   await toggle(false,'Enter');
   assert.ok((await snapshot()).blocked.write>0);
   assert.equal((await snapshot()).metadata,null);
   await screenshot('memory-hidden-write-refused');
   await toggle(true,'Space');await toggle(false);
   await reload(true);
   assert.equal((await snapshot()).metadata,null);
   await screenshot('reload-resurfaced');
   result.completed.push('metadata write failure hides in memory with honest message; reopen works; reload resurfaces');
  }else{
   assert.equal((await snapshot()).metadata,hiddenFor(rows),'A real saved dismissal is present behind the read refusal');
   assert.ok((await snapshot()).blocked.read>0);
   await screenshot('read-refused-visible');
   await toggle(false);await reload(true);
   assert.ok((await snapshot()).blocked.read>0);
   result.completed.push('metadata read failure cannot hide copies, including after a successful hide write and reload');
  }
  await invariant();await expect(composer).toHaveDraft(draft);
  result.passed=true;
 }catch(error){
  try{await screenshot('failure')}catch{}
  throw error;
 }finally{await context.close()}
}

async function saveResult(passed,error){
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({
  passed,evidence:'Real Vite UI; synthetic HTTP/SSE and key-scoped storage failures; fresh owned contexts only.',
  viewports:[{width:1280,height:900},{width:320,height:844}],cases,screenshots,
  complementaryJourney:'tests/delivery-reconciliation-browser.mjs (separate execution required)',
  ...(error?{failure:String(error.stack||error)}:{}),
 },null,2));
}
await mkdir(artifact,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},
  optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();origin=new URL(vite.resolvedUrls.local[0]).origin;
 browser=await chromium.launch({headless:true});
 for(const fault of ['none','write','read'])await runCase(fault);
 await saveResult(true);
 console.log(`Legacy outbox notice browser passed: ${cases.length} isolated cases; artifacts ${artifact}`);
}catch(error){await saveResult(false,error);throw error}
finally{try{if(browser)await browser.close()}finally{if(vite)await vite.close()}}