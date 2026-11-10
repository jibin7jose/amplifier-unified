import {showFixtureConversation} from './browser-chat-pages.mjs';
import {prepareCanvasReaderInput,recordCanvasInputFailure} from './canvas-reader-input.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {build} from 'esbuild';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {settingsButton} from './browser-settings.mjs';
const evidence=process.env.AMPLIFIER_TEST_OUTPUT||join(tmpdir(),'amplifier-smart-tools');await mkdir(evidence,{recursive:true});
const temp=await mkdtemp(join(tmpdir(),'unified-mcp-browser-'));
const source=`import {App} from '@modelcontextprotocol/ext-apps';
const app=new App({name:'Independent counter',version:'1.0.0'},{});
let hostUpdates=0,resumeReads=false,lastVisible;
window.enableFixtureResumeReads=value=>resumeReads=value;
window.fixtureRead=()=>app.callServerTool({name:'counter_read',arguments:{}});
const showHostContext=()=>{
 const context=app.getHostContext()||{},visible=context['com.microsoft.amplifier/visibility']!==false;
 document.body.dataset.theme=context.theme||'';document.body.dataset.displayMode=context.displayMode||'';document.body.dataset.hostUpdates=String(hostUpdates);document.body.dataset.visible=String(visible);
 if(resumeReads&&visible&&lastVisible===false)window.fixtureRead().then(()=>document.body.dataset.resumeReads=String(Number(document.body.dataset.resumeReads||0)+1)).catch(error=>document.body.dataset.resumeError=error.message);
 lastVisible=visible;
};
app.onhostcontextchanged=()=>{hostUpdates++;showHostContext();};
const draw=async result=>{const value=result.structuredContent;document.querySelector('#count').textContent=String(value.count);await app.updateModelContext({structuredContent:value});};
app.ontoolresult=draw;
document.querySelector('#add').onclick=async()=>draw(await app.callServerTool({name:'counter_add',arguments:{amount:1}}));
document.querySelector('#media').onclick=async()=>{
 try{const result=await app.readServerResource({uri:'counter://media/preview'});document.querySelector('#resource').textContent=result.contents[0].text}
 catch(error){document.querySelector('#resource').textContent=error.message}
};
await app.connect();
showHostContext();
await app.listServerResources();
await draw(await app.callServerTool({name:'counter_read',arguments:{}}));
document.querySelector('#isolation').textContent=(()=>{try{return parent.document.title}catch{return 'Parent isolated'}})();
fetch('/api/state').then(()=>document.body.dataset.network='FAILED').catch(()=>document.body.dataset.network='blocked');
`;
const built=await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'counter.js'},bundle:true,write:false,format:'esm',minify:true});
const attack=`<script>parent.parent.postMessage({jsonrpc:'2.0',id:'forged',method:'tools/call',params:{name:'counter_add',arguments:{amount:1000}}},'*')</script>`;
const html=`<!doctype html><html><body><h1>Independent counter</h1><output id="count">0</output><button id="add">Add one</button><button id="media">Read media</button><output id="resource"></output><input aria-label="Unsaved tool draft"><p id="isolation"></p><iframe sandbox="allow-scripts" srcdoc="${attack.replaceAll('&','&amp;').replaceAll('"','&quot;')}"></iframe><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script></body></html>`;
await writeFile(join(temp,'app.html'),html);
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/canvas_mcp_ui_server.py',import.meta.url)),join(temp,'app.html')],{stdio:'inherit'});
let browser,page,failure;
try{
 for(let i=0;i<150;i++){try{if((await fetch('http://127.0.0.1:8967/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1450,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message)});page.on('console',msg=>{if(msg.type()==='error')console.log('CONSOLE',msg.text().slice(0,700))});
 await page.addInitScript(()=>{
  let dark=true;
  const listeners=new Set();
  window.matchMedia=query=>({media:query,get matches(){return query==='(prefers-color-scheme: dark)'&&dark},addEventListener:(type,listener)=>{if(type==='change')listeners.add(listener)},removeEventListener:(type,listener)=>{if(type==='change')listeners.delete(listener)}});
  window.setSystemTheme=next=>{dark=next;for(const listener of listeners)listener({matches:dark,media:'(prefers-color-scheme: dark)'})};
 });
 await page.goto('http://127.0.0.1:8967/');await showFixtureConversation(page,{navPinned:false});await page.waitForSelector('#amp-one');
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const observations=async()=>{const response=await page.request.get('http://127.0.0.1:8967/fixture/smart-tool-observations');assert.equal(response.status(),200);return response.json()};
 const operations=async()=>(await observations()).operations;
 const operation=async id=>(await operations()).find(row=>row.id===id);
 const appearance=async()=>{
  const navigation=page.getByRole('button',{name:'Open navigation',exact:true});
  if(await navigation.isVisible())await navigation.click();
  await settingsButton(page).click();
  await page.locator('[data-settings-section="appearance"]').click();
 };
 await action('view.update',{patch:{scheme:'system'}});
 await action('canvas.show',{kind:'markdown',title:'Pinned notes',content:'# Notes beside the live tool'});
 const pinned=(await action('canvas.views.inspect',{})).result.views[0];
 const opened=await action('smartTools.open',{id:'counter',tool:'counter_read'});
 await page.waitForFunction(()=>window.amplifier.getState().canvas.kind==='mcp-app');
 const frame=page.frameLocator('.a-canvas-html');
 try{await frame.getByText('Parent isolated',{exact:true}).waitFor({timeout:12000})}catch(e){console.log(await page.locator('.a-canvas-body').innerText());console.log(await frame.locator('body').innerText());throw e}
 assert.equal(await frame.locator('body').getAttribute('data-theme'),'dark','System appearance must initialize the official MCP App with the resolved dark theme');
 assert.equal(await frame.locator('body').getAttribute('data-display-mode'),'inline','Theme updates must retain the complete host context');
 const stableFrame=await page.locator('.a-canvas-html').boundingBox();
 await frame.getByRole('button',{name:'Read media'}).click();
 await frame.locator('#resource').filter({hasText:'Retained preview'}).waitFor();
 let toolInFlight=false,polledCalls=0,markToolStarted,releaseTool;
 const toolStarted=new Promise(resolve=>markToolStarted=resolve),toolGate=new Promise(resolve=>releaseTool=resolve);
 const toolCallURL=url=>/^\/api\/canvas\/[^/]+\/tools\/call$/.test(url.pathname);
 page.on('request',req=>{if(req.url().includes('/api/smart-tools/operations/'))polledCalls++});
 await page.route(toolCallURL,async route=>{toolInFlight=true;markToolStarted();await toolGate;await route.continue()});
 const readsBefore=polledCalls;
 await frame.getByRole('button',{name:'Add one'}).click();
 await Promise.race([toolStarted,new Promise((_,reject)=>setTimeout(()=>reject(Error('Tool request did not reach the route gate')),5000))]);
 assert.equal(toolInFlight,true);
 assert.equal(await page.locator('.a-mcp-status').getAttribute('data-phase'),'ready','Interaction must not flicker the host render status');
 assert.equal(await page.locator('.a-mcp-status').isVisible(),false);
 assert.equal((await page.locator('.a-canvas-html').boundingBox()).y,stableFrame.y);

 releaseTool();
 await frame.locator('#count').filter({hasText:/^1$/}).waitFor();
 assert.equal(polledCalls,readsBefore,'Completed interactive calls return directly without operation polling');
 await page.unroute(toolCallURL);
 await page.waitForFunction(()=>window.amplifier.getState().canvas.mcp.context.structuredContent?.count===1);
 assert.equal(await frame.locator('body').getAttribute('data-network'),'blocked');
 let state=await page.evaluate(()=>window.amplifier.getState());const savedId=state.canvas.id;
 assert.equal((await operations()).filter(o=>o.target?.name==='counter_add').length,1,'Nested content cannot call host tools');
 // The panel paints optimistically, but the live MCP frame must not resume
 // reads until the matching shared visibility action has been acknowledged.
 const visibilityRequests=[],visibilityReports=[];let nextVisibility;
 page.on('request',request=>{
  if(request.url().includes('/tools/call'))visibilityRequests.push(request.postDataJSON());
  if(request.url().endsWith('/api/actions')){const body=request.postDataJSON();if(body.args?.action==='canvas.report')visibilityReports.push(body.args.args)}
 });
 const waitVisibility=()=>new Promise(resolve=>nextVisibility=resolve);
 const holdVisibility=route=>{
  if(route.request().postDataJSON()?.action==='canvas.visibility'){nextVisibility(route);return}
  return route.continue();
 };
 await page.route('**/api/actions',holdVisibility);
 await frame.getByRole('textbox',{name:'Unsaved tool draft'}).fill('Keep this unsaved');
 await frame.locator('body').evaluate(()=>window.enableFixtureResumeReads(true));
 const hideRequest=waitVisibility();await page.getByRole('button',{name:'Hide canvas',exact:true}).click();const hideRoute=await hideRequest;
 await page.locator('#workspace-canvas').waitFor({state:'hidden'});
 await frame.locator('body[data-visible="false"]').waitFor({state:'attached'});
 await hideRoute.continue();await page.waitForFunction(()=>!window.amplifier.getState().canvas.visibilityPending&&!window.amplifier.getState().canvas.open);
 const restoreRequest=waitVisibility();
 await page.evaluate(()=>{window.fixtureRestore=window.amplifier.dispatch('canvas.visibility',{open:true})});const restoreRoute=await restoreRequest;
 await page.locator('#workspace-canvas').waitFor({state:'visible'});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.visibilityPending),true);
 assert.equal(await frame.locator('body').getAttribute('data-visible'),'false','An optimistic panel is not an acknowledged tool view');
 const paused=await frame.locator('body').evaluate(async()=>{try{await window.fixtureRead();return 'unexpectedly sent'}catch(error){return error.message}});
 assert.match(paused,/paused/);assert.equal(visibilityRequests.length,0,'Reads must not reach the closed server binding while acknowledgement is delayed');
 await restoreRoute.continue();await page.evaluate(()=>window.fixtureRestore);
 await frame.locator('body[data-resume-reads="1"]').waitFor();
 assert.equal(await frame.locator('body').getAttribute('data-resume-error'),null);
 assert.equal(visibilityRequests.length,1);assert.equal(visibilityRequests[0].name,'counter_read');
 assert.equal(visibilityReports.filter(row=>row.status==='error').length,0,'Expected suspension must not publish a host error');
 assert.equal(await frame.getByRole('textbox',{name:'Unsaved tool draft'}).inputValue(),'Keep this unsaved');
 await frame.locator('body').evaluate(()=>window.enableFixtureResumeReads(false));
 await page.unroute('**/api/actions',holdVisibility);
 await expect.poll(async()=>(await operation(visibilityRequests[0].id))?.status).toBe('completed');
 state=await page.evaluate(()=>window.amplifier.getState());
 // System changes update the existing bridge. They must neither replace the
 // iframe nor replay the app's startup tool request.
 const themeOperationCount=(await operations()).length;
 await frame.locator('body').evaluate(el=>el.dataset.themeMarker='same-frame');
 await page.evaluate(()=>window.setSystemTheme(false));
 await frame.locator('body[data-theme="light"]').waitFor();
 assert.equal(await frame.locator('body').getAttribute('data-theme-marker'),'same-frame');
 assert.equal((await operations()).length,themeOperationCount);
 await action('view.update',{patch:{scheme:'dark'}});
 await frame.locator('body[data-theme="dark"]').waitFor();
 await page.evaluate(()=>window.setSystemTheme(false));
 assert.equal(await frame.locator('body').getAttribute('data-theme'),'dark','An explicit dark preference must override system media changes');
 await action('view.update',{patch:{scheme:'light'}});
 await frame.locator('body[data-theme="light"]').waitFor();
 await page.evaluate(()=>window.setSystemTheme(true));
 assert.equal(await frame.locator('body').getAttribute('data-theme'),'light','An explicit light preference must override system media changes');
 assert.equal((await operations()).length,themeOperationCount);
 await action('view.update',{patch:{scheme:'system'}});
 await frame.locator('body[data-theme="dark"]').waitFor();
 // Layout changes must not reconnect the MCP App or replay its startup tools.
 const operationCount=(await operations()).length;
 await frame.locator('body').evaluate(el=>el.dataset.liveMarker='same-frame');
 await action('view.update',{patch:{canvasWidth:1000,canvasFocused:true,canvasControlsExpanded:false}});
 await page.mouse.move(500,400);
 await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').getBoundingClientRect().width===innerWidth);
 assert.equal(await frame.locator('body').getAttribute('data-live-marker'),'same-frame');
 await expect(page.locator('.a-canvas-artifact-toolbar')).toBeVisible();
 const geometry=await page.locator('#workspace-canvas').evaluate(panel=>{
  const rect=selector=>{const r=panel.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};
  return {viewport:{width:innerWidth,height:innerHeight},detailsHidden:panel.querySelector('#canvas-options').hidden,
   readerInert:panel.querySelector('.a-canvas-reader').inert,chrome:rect('.a-canvas-chrome'),
   toolbar:rect('.a-canvas-artifact-toolbar'),reader:rect('.a-canvas-reader'),body:rect('.a-canvas-body'),frame:rect('.a-canvas-html')};
 });
 const assertFrameBoundary=g=>{
  assert.equal(g.detailsHidden,true);assert.equal(g.readerInert,false);
  assert.equal(g.frame.x,0);assert.equal(g.frame.width,1450);
  assert.equal(g.reader.y,g.chrome.bottom);assert.equal(g.toolbar.y,g.reader.y);
  assert.equal(g.frame.y,g.toolbar.bottom,'LIVE_TOOL_BOUNDARY begins immediately below the visible artifact toolbar');
  assert.deepEqual(g.frame,g.body,'The live iframe fills the reader body');
  assert.equal(g.body.bottom,g.reader.bottom);assert.equal(g.reader.bottom,g.viewport.height);
  assert.ok(g.frame.height>=250,'The focused live tool retains usable vertical space');
 };
 await writeFile(join(evidence,'focused-live-tool-geometry.json'),JSON.stringify(geometry,null,2));
 assertFrameBoundary(geometry);
 await page.screenshot({path:join(evidence,'focused-live-tool-geometry.png')});
 // The obsolete nav-only boundary is rejected by this same adjacency oracle.
 const obsolete={...geometry,frame:{...geometry.frame,y:geometry.chrome.bottom}};
 assert.throws(()=>assertFrameBoundary(obsolete),/LIVE_TOOL_BOUNDARY/);
 await writeFile(join(evidence,'focused-live-tool-boundary-negative.json'),JSON.stringify({geometry:obsolete,rejected:true},null,2));
 await action('view.update',{patch:{canvasFocused:false}});
 assert.equal(await frame.locator('body').getAttribute('data-live-marker'),'same-frame');
 assert.equal((await operations()).length,operationCount);

 // An agent takes the same action through app_control's shared dispatch contract.
 const receipt=await action('smartTools.call',{id:'counter',name:'counter_add',arguments:{amount:4,delay_ms:3000}});
 await expect.poll(async()=>(await operation(receipt.operationId))?.status).toBe('running');
 await appearance();
 await page.getByLabel('Color mode',{exact:true}).selectOption('light');
 await frame.locator('body[data-theme="light"]').waitFor();
 await page.getByText('Layout & background',{exact:true}).click();
 await page.locator('#layout').selectOption('work');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await action('view.update',{patch:{canvasFocused:true}});
 assert.equal(await frame.locator('body').getAttribute('data-live-marker'),'same-frame');
 assert.ok((await observations()).canvasArtifacts.some(row=>row.id===pinned.resourceId),'Opening a tool retains earlier saved artifacts');
 await expect.poll(async()=>(await operation(receipt.operationId))?.status).toBe('completed');
 state=await page.evaluate(()=>window.amplifier.getState());assert.equal((await operation(receipt.operationId)).result.structuredContent.count,5);
 assert.equal((await operations()).filter(o=>o.target?.name==='counter_add').length,2,'Presentation changes must not replay accepted tool work');
 await action('view.update',{patch:{canvasFocused:false}});
 await appearance();
 await page.getByLabel('Color mode',{exact:true}).selectOption('system');
 await page.getByText('Layout & background',{exact:true}).click();
 await page.locator('#layout').selectOption('balanced');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await action('canvas.close',{});
 const reopen=action('canvas.select',{id:savedId});
 await page.evaluate(()=>window.setSystemTheme(false));
 await reopen;
 await frame.locator('body[data-theme="light"]').waitFor();
 await frame.locator('#count').filter({hasText:/^5$/}).waitFor();
 // Explicit selection can mount a new saved document. Bind this Details
 // round trip to that current document, not a marker from before selection.
 const reopenedBody=await frame.locator('body').elementHandle();assert.ok(reopenedBody);
 try{
  await page.getByRole('button',{name:'Canvas details',exact:true}).click();
  await frame.locator('#count').filter({hasText:/^5$/}).waitFor();
  await page.screenshot({path:join(evidence,'canvas.png')});
  await prepareCanvasReaderInput(page,{target:frame.getByRole('button',{name:'Add one',exact:true}),label:'smart-tools-stale-config',outputDir:evidence});
  const sameBody=await frame.locator('body').evaluate((el,original)=>el===original,reopenedBody);
  const count=await frame.locator('#count').textContent();
  await writeFile(join(evidence,'reopened-details-identity.json'),JSON.stringify({sameBody,count},null,2));
  assert.equal(sameBody,true,'Details open/close retains the currently reopened tool document');
  assert.equal(count,'5','Details open/close retains the shared counter');
 }finally{await reopenedBody.dispose()}
 assert.equal((await operations()).filter(o=>o.target?.name==='counter_add').length,2,'Closing Details does not replay tool work');
 // A saved view cannot silently acquire a replacement server configuration.
 await action('smartTools.configure',{id:'counter',name:'Changed counter',command:'invalid-command',args:[]});
 await expect.poll(async()=>(await observations()).servers.find(s=>s.id==='counter')?.command).toBe('invalid-command');
 await frame.getByRole('button',{name:'Add one'}).click();
 await page.getByText('This server configuration changed. The saved document is retained; review the connection in Settings and open a new tool view.',{exact:true}).waitFor();
 await frame.getByRole('button',{name:'Read media'}).click();
 await frame.locator('#resource').filter({hasText:'configuration changed'}).waitFor();
 assert.deepEqual(errors.filter(e=>!e.includes('configuration changed')),[]);
 console.log('MCP Apps browser passed: official SDK handshake, shared tool actions and view state, sandbox isolation, nested-message rejection, durable reopen without mutation replay, stale-config denial.');
}catch(error){failure=await recordCanvasInputFailure(page,error,{outputDir:evidence,label:'smart-tools'});throw failure}
finally{
 const cleanup=[];
 for(const close of [()=>browser?.close(),async()=>fixture.kill(),()=>rm(temp,{recursive:true,force:true})])try{await close()}catch(error){cleanup.push(error)}
 if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Smart Tools cleanup failure',{cause:failure||cleanup[0]});
}
