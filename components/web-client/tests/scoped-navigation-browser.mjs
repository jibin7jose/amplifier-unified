import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Production host/assets and client navigation, with two synthetic workspaces.
// No model calls, timing ceilings, or private application state are involved.
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-scoped-navigation';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 [fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls','--scoped-query-control'],{stdio:['ignore','pipe','pipe']});
const base=['pinnedNavigation','recentNavigation','workspaceShortcuts'];
const requested=extra=>[...base,...extra].sort();
let browser,page,fixtureHome,fixtureUrl,queryProof=[],allowSearch=()=>{},releaseSearch=()=>{},releaseReplacement=()=>{},primaryError;
let stderr='';fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-16384)});
const samples=[],checks=[],errors=[],responseTasks=[],frames=[];let bootstrapCount=0,subscriptionCount=0,expectRetryCooldown=false;
function shape(shell,resources){
 const builtin=(shell.resolvedInstances||[]).filter(row=>['builtin.chats','builtin.workspaces'].includes(row.package));
 assert.ok(builtin.length,'Built-in navigation must be present');
 for(const instance of builtin){
  const snapshot=shell.snapshots[instance.id];
  assert.deepEqual(snapshot.navigationResources,resources);
  for(const key of ['pinnedNavigation','recentNavigation','workspaceShortcuts','recentShortcuts','recentScope'])assert.ok(Object.hasOwn(snapshot,key),key);
  for(const key of ['chatNavigation','homeNavigation','workspaceOverview'])assert.equal(Object.hasOwn(snapshot,key),false,key+' was not requested');
  assert.equal(Object.hasOwn(snapshot,'workspaceExplorer'),resources.includes('workspaceExplorer'));
  assert.ok(Object.hasOwn(snapshot.sidebarNavigation,'recentView'));
  for(const [key,resource] of [['search','sidebarSearch'],['workspace','sidebarWorkspace'],['recent','sidebarRecent'],['pinned','sidebarPinned']])
   assert.equal(Object.hasOwn(snapshot.sidebarNavigation,key),resources.includes(resource),key+' coverage');
 }
}
try{
 await mkdir(output,{recursive:true});
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout\n'+stderr)),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+'\n'+stderr))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 fixtureUrl=url;
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  const Native=window.WebSocket;window.shellProofSockets=[];
  window.WebSocket=class extends Native{constructor(...args){super(...args);if(String(args[0]).includes('/api/events/'))window.shellProofSockets.push(this)}};
 });
 page.on('websocket',socket=>socket.on('framereceived',event=>{
  if(frames.length>=96)return;
  try{const value=JSON.parse(String(event.payload)),changes=value.data?.changes||[];
   frames.push({event:value.event,bytes:Buffer.byteLength(event.payload),shellChanges:changes.filter(row=>row.path?.[0]==='shellResources').map(row=>row.path.slice(0,7))});
  }catch{}
 }));
 page.on('response',response=>{
  const query=new URL(response.url());if(!['/api/shell','/api/shell/resources'].includes(query.pathname))return;
  const bootstrap=query.pathname==='/api/shell';if(bootstrap)bootstrapCount++;else subscriptionCount++;
  if(samples.length>=96)return;
  const demand=bootstrap?{resources:JSON.parse(query.searchParams.get('navigationResources')),demandId:Number(query.searchParams.get('demandId'))}:response.request().postDataJSON();
  const sample={resources:demand.resources,demandId:demand.demandId,bootstrap,status:response.status()};samples.push(sample);
  responseTasks.push((async()=>{try{
   const text=await response.text(),wire=JSON.parse(text),envelope=bootstrap?wire.shellResources:wire;sample.bytes=Buffer.byteLength(text);
   if(response.status()===429){assert.ok(expectRetryCooldown&&!bootstrap&&demand.reason==='manual');sample.valid=true;sample.cooldown=true;return}
   if(bootstrap){assert.equal(Object.hasOwn(wire,'snapshots'),false);assert.ok(wire.instanceGenerations);assert.ok(wire.shellBootstrapKey)}
   assert.equal(envelope.demandId,demand.demandId);
   for(const entry of Object.values(envelope.instances)){
    assert.ok(Number.isSafeInteger(entry.stateRevision));assert.ok(entry.resources.context);
    assert.deepEqual([...new Set([...Object.keys(entry.resources),...Object.keys(entry.pending)])].sort(),['context',...demand.resources].sort());
    for(const value of Object.values(entry.pending))assert.ok(['pending','retry'].includes(value.status));
   }
   sample.valid=true;sample.keys=Object.keys(envelope.instances);
  }catch(error){sample.error=String(error)}})());
 });
 const act=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState());
 const waitCoverage=async extra=>{
  const resources=requested(extra);
  let observed;
  await expect.poll(async()=>{observed=await shell();return (observed?.resolvedInstances||[]).filter(row=>['builtin.chats','builtin.workspaces'].includes(row.package)).map(row=>{const snapshot=observed.snapshots[row.id];return Object.keys(snapshot?.navigationResourcePending||{}).length?[]:snapshot?.navigationResources})}).toEqual([resources,resources]);
  shape(observed,resources);checks.push({resources});
 };
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await expect(composer).toBeVisible();await waitCoverage([]);
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[],chats=[];fixtureHome=home;
 for(const name of ['Scoped A','Scoped B']){
  const folder=path.join(home,name);await mkdir(folder);await writeFile(path.join(folder,name+'.txt'),'Synthetic file');
  await act('workspace.add',{path:folder,name});
  const workspace=(await act('workspace.list',{query:name})).result.items.find(row=>row.path===folder);assert.ok(workspace);workspaces.push(workspace);
  chats.push((await act('session.create',{workspace:folder,title:name+' chat'})).result.sessionId);
 }
 // A small real library makes Load older exercise the receipt→stream boundary.
 for(let index=0;index<21;index++){
  await act('session.create',{workspace:workspaces[0].path,title:'Recent resource '+index});await waitCoverage([]);
 }
 await act('session.select',{id:chats[0]});await act('session.pin',{id:chats[0],pinned:true});await act('view.update',{patch:{navPinned:true}});
 await composer.fill('Keep this unsent scoped-navigation draft');
 await act('canvas.show',{kind:'html',title:'Scoped navigation canvas',content:'<input aria-label="Local canvas draft">'});
 await page.frameLocator('.a-canvas-html').getByRole('textbox').fill('preserved canvas input');
 await page.evaluate(()=>{window.scopedDocument=document.querySelector('#amp-one');window.scopedFrame=document.querySelector('.a-canvas-html')});
 await waitCoverage([]);
 const recent=page.locator('[data-shell-instance="chats"] [data-sidebar-section="recent"]');
 await expect(recent.locator('[data-session-id]')).toHaveCount(20);
 const beforeRecent={bootstrapCount,subscriptionCount};
 await recent.getByRole('button',{name:'Load older chats',exact:true}).click();
 // A changed query omits its old page while the exact replacement is pending.
 await expect.poll(async()=>(await shell())?.snapshots?.chats?.recentNavigation?.scope?.limit).toBe(40);
 await expect.poll(()=>recent.locator('[data-session-id]').count()).toBeGreaterThan(20);
 await expect(recent.getByRole('alert')).toHaveCount(0);assert.deepEqual({bootstrapCount,subscriptionCount},beforeRecent);
 checks.push({recentReceiptWaitsForStream:true,noRecentReadAfterCommand:true});
 const body=page.locator('.a-work-browser');
 const browse=(surface,tab)=>act('view.update',{patch:{workSurface:surface,workWorkspaceId:workspaces[1].id,...(tab?{workWorkspaceTab:tab}:{})}});
 // Hold a real Search result, then hold its replacement. An old result cannot
 // publish coverage for a newer screen while the current result is pending.
 let searchHeld=false,searchResponseReady=false,replacementHeld=false,searchReleased=false,replacementSettled=false;
 const startSearch=new Promise(resolve=>{allowSearch=resolve});
 const searchGate=new Promise(resolve=>{releaseSearch=()=>{searchReleased=true;resolve()}});
 const replacementGate=new Promise(resolve=>{releaseReplacement=resolve});
 const holdRoute=async route=>{
  const resources=route.request().postDataJSON().resources;
  if(resources?.includes('sidebarSearch')&&!searchHeld){searchHeld=true;await startSearch;const response=await route.fetch();searchResponseReady=true;await searchGate;return route.fulfill({response})}
  if(searchReleased&&!replacementHeld&&resources?.length===base.length){replacementHeld=true;await replacementGate;const response=await route.fetch();await route.fulfill({response});replacementSettled=true;return}
  return route.continue();
 };
 const hold=route=>holdRoute(route).catch(error=>{errors.push('Held route: '+String(error))});
 await page.route('**/api/shell/resources',hold);
 await page.getByRole('button',{name:'Search chats',exact:true}).click();await expect.poll(()=>searchHeld).toBe(true);
 await expect(body.locator('.a-search-result-summary').getByRole('status')).toHaveText('Searching…');
 await expect(body.locator('.a-search-result-summary').getByRole('status')).toBeVisible();
 await expect(body.getByText('No chats found.',{exact:true})).toHaveCount(0);
 allowSearch();await expect.poll(()=>searchResponseReady).toBe(true);
 // A real state publication carries the registered resource baseline even while
 // its HTTP response is held. The subsequent scope must reject that old reply.
 await act('view.update',{patch:{navExpanded:true}});await waitCoverage(['sidebarSearch']);
 await browse('workspace','files');await expect.poll(async()=>(await state()).view.workWorkspaceTab).toBe('files');
 releaseSearch();await expect.poll(()=>replacementHeld).toBe(true);
 for(const snapshot of Object.values((await shell()).snapshots)){assert.deepEqual(snapshot.navigationResources,[]);assert.equal(snapshot.sidebarNavigation?.search,undefined)}
 assert.equal((await state()).view.workSurface,'workspace');
 releaseReplacement();await expect.poll(()=>replacementSettled).toBe(true);await page.unroute('**/api/shell/resources',hold);await waitCoverage([]);
 await expect(body.getByRole('button',{name:'Scoped B.txt',exact:true})).toBeVisible();
 checks.push({staleSearchIgnored:true,loadingBeforeFirstPage:true});
 await body.getByRole('button',{name:'Chats',exact:true}).click();await waitCoverage(['sidebarWorkspace']);
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped B chat',exact:true})).toBeVisible();
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped A chat',exact:true})).toHaveCount(0);
 await body.getByRole('button',{name:'Details',exact:true}).click();await waitCoverage([]);await expect(body.getByText(workspaces[1].path,{exact:true})).toBeVisible();
 await body.getByRole('button',{name:'Files',exact:true}).click();await waitCoverage([]);await expect(body.getByRole('button',{name:'Scoped B.txt',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Search chats',exact:true}).click();await waitCoverage(['sidebarSearch']);
 for(const title of ['Scoped A chat','Scoped B chat'])await expect(body.locator('[data-session-id]').getByRole('button',{name:title,exact:true})).toBeVisible();
 // Hold only fixture-owned Search computation; the real process runs after
 // release. Existing rows stay visible, reads never replay a user command, and
 // an explicit retry settles through the normal resource owner and stream.
 const queryControl=async mode=>{
  const response=await page.request.post(url+'/fixture/scoped-query',{data:{mode,clientId:(await state()).client.id}});
  assert.equal(response.status(),200);const proof=await response.json();queryProof.push(proof);return proof;
 };
 const retainedRow=body.locator('[data-session-id]').getByRole('button',{name:'Scoped B chat',exact:true});
 const retainedBox=await retainedRow.boundingBox();
 assert.equal((await queryControl('hold')).catalogPaused,true);
 await act('session.rename',{id:chats[1],title:'Scoped B refreshed'});
 await expect.poll(async()=>(await (await page.request.get(url+'/fixture/scoped-query')).json()).entered).toBeGreaterThan(0);
 await expect(body.getByRole('status').filter({hasText:'Updating chat search'})).toHaveCount(0);
 await expect(retainedRow).toBeVisible();
 assert.deepEqual(await retainedRow.boundingBox(),retainedBox,'Background refresh preserves the visible row geometry');
 // Canvas is intentionally retained but hidden on Search. Exercise a visible
 // local disclosure without changing the held query's demand or forcing input
 // into the hidden frame; exact Canvas draft conservation is checked below.
 const beforeDisclosureRequests=subscriptionCount;
 const filters=body.locator('.a-search-filters');
 await filters.locator('summary').click();
 await expect(filters.getByRole('combobox',{name:'Filter chats by location',exact:true})).toBeVisible();
 await filters.locator('summary').click();
 await expect(filters.getByRole('combobox',{name:'Filter chats by location',exact:true})).toBeHidden();
 assert.equal(subscriptionCount,beforeDisclosureRequests);
 await queryControl('fail');
 await expect(body.getByRole('button',{name:'Retry chat search',exact:true})).toBeVisible();
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped B chat',exact:true})).toBeVisible();
 const failedProof=await queryControl('hold'),priorEntered=failedProof.entered;
 const failedKey=failedProof.events.findLast(event=>event.targeted&&event.failureFenceCurrent===true)?.key;
 assert.ok(failedKey,'Record the exact failed current query before checking retry suppression');
 expectRetryCooldown=true;
 const rejectedRetry=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/shell/resources'&&response.request().postDataJSON()?.reason==='manual');
 await body.getByRole('button',{name:'Retry chat search',exact:true}).click();
 assert.equal((await rejectedRetry).status(),429);
 await expect(body.getByRole('alert')).toContainText('Navigation resources could not be refreshed');
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped B chat',exact:true})).toBeVisible();
 const beforeCooldownRequests=subscriptionCount;
 // Exercise the documented five-second refusal window; no polling POST or
 // automatic retry is allowed when the window expires.
 await page.waitForTimeout(5100);
 assert.equal(subscriptionCount,beforeCooldownRequests);
 const heldProof=await (await page.request.get(url+'/fixture/scoped-query')).json();
 queryProof.push(heldProof);
 assert.equal(heldProof.catalogPaused,true);
 assert.deepEqual(heldProof.currentFence,failedProof.currentFence,'Cooldown observes the same canonical query generation');
 assert.equal(heldProof.entered,priorEntered);
 assert.equal(heldProof.events.filter(event=>event.key===failedKey).length,1,'The failed query must not execute automatically');
 assert.ok(heldProof.events.some(event=>event.targeted&&event.failureFenceCurrent===true),'The failed current query must belong to the visible Chats instance');
 expectRetryCooldown=false;
 await body.getByRole('button',{name:'Retry chat search',exact:true}).click();
 await expect.poll(async()=>(await (await page.request.get(url+'/fixture/scoped-query')).json()).entered).toBeGreaterThan(priorEntered);
 const retriedProof=await queryControl('release');
 assert.equal(retriedProof.catalogPaused,false);
 assert.equal(retriedProof.events.filter(event=>event.key===failedKey).length,2,'The same query executes once more only after explicit Retry');
 await waitCoverage(['sidebarSearch']);
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped B refreshed',exact:true})).toBeVisible();
 await expect(body.locator('[data-session-id]').getByRole('button',{name:'Scoped B chat',exact:true})).toHaveCount(0);
 checks.push({heldQueryRetainsRows:true,queryFailureExplicitRetry:true,queryWaitKeepsVisibleControlsResponsive:true});
 // A failed first page is unavailable, not still loading or a proven empty list.
 await queryControl('fail');
 await body.getByRole('searchbox',{name:'Search chats',exact:true}).fill('Cold failed search');
 await expect(body.getByRole('button',{name:'Retry chat search',exact:true})).toBeVisible();
 await expect(body.getByRole('status').filter({hasText:'Search unavailable'})).toBeVisible();
 await expect(body.getByRole('status').filter({hasText:/Searching|Loading/})).toHaveCount(0);
 await expect(body.getByRole('heading',{name:'No chats found',exact:true})).toHaveCount(0);
 await expect(body.locator('[data-session-id]')).toHaveCount(0);
 await queryControl('release');
 await body.getByRole('searchbox',{name:'Search chats',exact:true}).fill('');
 await waitCoverage(['sidebarSearch']);
 checks.push({coldFailureIsUnavailableNotLoadingOrEmpty:true});
 await browse('workspaces');await waitCoverage(['workspaceExplorer']);await expect(body.getByRole('heading',{name:'All workspaces',exact:true})).toBeVisible();
 await act('session.select',{id:chats[0]});await waitCoverage([]);await expect(composer).toHaveDraft('Keep this unsent scoped-navigation draft');
 assert.equal(await page.evaluate(()=>window.scopedDocument===document.querySelector('#amp-one')&&window.scopedFrame===document.querySelector('.a-canvas-html')),true);
 await expect(page.frameLocator('.a-canvas-html').getByRole('textbox')).toHaveValue('preserved canvas input');
 await expect(body.getByRole('heading'),'Hidden WorkBrowser must not render an expensive page').toHaveCount(0);
 // Same-scope commands receive their data through the stream. Neither their
 // receipts nor reconnecting can trigger another descriptor or catalog read.
 const before={bootstrapCount,subscriptionCount};
 for(const navExpanded of [false,true,false,true])await act('view.update',{patch:{navExpanded}});
 await expect.poll(async()=>(await state()).view.navExpanded).toBe(true);
 assert.deepEqual({bootstrapCount,subscriptionCount},before);
 const previousConnections=await page.evaluate(()=>window.shellProofSockets.length);
 assert.ok(previousConnections>0,'The fixture must exercise the normal WebSocket lane');
 await page.evaluate(()=>window.shellProofSockets.at(-1).close());
 await expect.poll(()=>page.evaluate(()=>window.shellProofSockets.length)).toBeGreaterThan(previousConnections);
 await waitCoverage([]);
 assert.equal(bootstrapCount,before.bootstrapCount,'Reconnect restores the stream baseline without a shell GET');
 assert.equal(subscriptionCount,before.subscriptionCount,'A retained registration does not need a repeated POST');
 const diagnostic=await page.locator('html').getAttribute('data-amplifier-shell-diagnostics');assert.ok(diagnostic&&diagnostic.length<=8192);
 assert.ok(JSON.parse(diagnostic).counts.reconnect>=2);
 assert.equal(bootstrapCount,1,'Only one bootstrap for all ordinary navigation and commands');
 assert.ok(frames.some(row=>row.event==='state-delta'&&row.shellChanges.length),'Changed resources use the existing state delta lane');
 checks.push({singleBootstrap:true,noCommandRefresh:true,reconnectBaseline:true,resourceDelta:true,diagnostics:JSON.parse(diagnostic)});
 assert.deepEqual((await (await page.request.get(url+'/fixture')).json()).sent,[]);assert.deepEqual(errors,[]);
 await Promise.all(responseTasks);assert.deepEqual(samples.filter(row=>!row.valid),[]);assert.ok(samples.some(row=>row.resources.includes('sidebarSearch'))&&samples.some(row=>row.resources.includes('sidebarWorkspace'))&&samples.some(row=>row.resources.includes('workspaceExplorer')));
 await page.screenshot({path:path.join(output,'preserved-chat.png')});
 checks.push({draftAndCanvasPreserved:true,hiddenBrowserUnmountedBody:true,noModelCalls:true});
 const beforeReload=await state(),oldClient=beforeReload.client.id,canvasId=beforeReload.canvas.id;
 await page.reload();await expect(composer).toHaveDraft('Keep this unsent scoped-navigation draft');await waitCoverage([]);
 assert.notEqual((await state()).client.id,oldClient);assert.equal((await state()).canvas.id,canvasId);
 await expect(page.frameLocator('.a-canvas-html').getByRole('textbox')).toBeVisible();
 assert.equal(bootstrapCount,2,'A new document owns its own bootstrap and demand sequence');
 await Promise.all(responseTasks);assert.deepEqual(samples.filter(row=>!row.valid),[]);
 checks.push({reloadNewClient:true,reloadDraftAndCanonicalCanvasPreserved:true});
 console.log(JSON.stringify({passed:true,checks,requestCount:samples.length}));
}catch(error){if(page&&fixtureUrl)try{queryProof.push(await (await page.request.get(fixtureUrl+'/fixture/scoped-query',{timeout:2000})).json())}catch{};primaryError=error;console.error(error);if(page)await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});throw error}
finally{
 allowSearch();releaseSearch();releaseReplacement();
 await Promise.allSettled(responseTasks);
 const cleanupErrors=[];
 try{await browser?.close()}catch(error){cleanupErrors.push(String(error));console.error('Browser cleanup failed',error)}
 try{if(fixture.exitCode===null&&fixture.signalCode===null){fixture.kill('SIGTERM');await new Promise(resolve=>{const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})})}}catch(error){cleanupErrors.push(String(error));console.error('Fixture cleanup failed',error)}
 if(cleanupErrors.length&&!primaryError)process.exitCode=1;
 await writeFile(path.join(output,'scoped-navigation.json'),JSON.stringify({passed:!primaryError&&!cleanupErrors.length,checks,samples,frames,queryProof,bootstrapCount,subscriptionCount,pageErrors:errors,error:primaryError?.stack,cleanupErrors,fixtureHome,fixturePid:fixture.pid,fixtureStderr:stderr},null,2)).catch(error=>{console.error('Evidence write failed',error);if(!primaryError)process.exitCode=1});
}
