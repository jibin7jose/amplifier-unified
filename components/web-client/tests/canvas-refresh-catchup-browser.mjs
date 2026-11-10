// Production host/build, real transport frames and synthetic documents only.
// Dropped incoming frames simulate a missed publication; no fake state/receipt.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks');
await mkdir(base,{recursive:true});const output=await mkdtemp(join(base,'canvas-catchup-'));
const report={status:'running',checks:[],errors:[],screenshots:[],actions:[],limits:['Synthetic runtime, no model or tool executions.','Lost incoming WebSocket frames are test-controlled; HTTP reads remain live.','Screenshots are rendered evidence, not an automatic visual-review claim.']};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,stdout='',stderr='',fatal,held=false,dropped=0,connections=0,sockets=[];
fixture.stderr.on('data',chunk=>stderr+=chunk);
const capture=async name=>{await page.screenshot({path:join(output,name+'.png')});report.screenshots.push(name+'.png')};
const check=async(name,run)=>{if(process.env.CANVAS_LOAD_FEEDBACK_ONLY&&!name.startsWith('Load feedback:'))return;const row={name,status:'running'};report.checks.push(row);try{row.observed=await run(row);row.status='passed'}catch(error){row.status='failed';row.error=error.stack||String(error);row.surface=await page.evaluate(()=>({inspector:document.querySelector('#canvas-options')?.outerHTML,freshness:document.querySelector('.a-canvas-freshness')?.outerHTML,view:window.amplifier.getState().canvasWorkspace,layout:window.amplifier.getState().view.canvasControlsExpanded}));await capture('failure-'+report.checks.length)}};
const html=version=>`<h1>Executable version ${version}</h1><label>Local unfinished input <input id="edit"></label><output id="executed"></output><script>document.getElementById('executed').textContent='executed-v${version}-'+crypto.randomUUID()</script>`;
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),20000);fixture.once('error',error=>{clearTimeout(timer);reject(error)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n'))try{const v=JSON.parse(line);if(v.url){clearTimeout(timer);resolve(v.url)}}catch{}})});
 browser=await chromium.launch({headless:true});report.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1500,height:950},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.routeWebSocket(/\/api\/events\/ws(?:\?|$)/,route=>{
  const server=route.connectToServer();connections++;sockets.push({route,server});
  server.onMessage(message=>{if(held){dropped++;return}route.send(message)});
 });
 page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>report.errors.push(error.message));
 const unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')report.actions.push(unwrap(request.postDataJSON()).action)});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=async(name,args={})=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result.accepted,true,JSON.stringify(result));return result};
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const view=()=>state().then(s=>s.canvasWorkspace.views.find(v=>v.viewId==='primary'));
 const target=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]));
 await action('session.create');const sessionId=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasWidth:800,canvasControlsExpanded:false,draft:'Catchup must retain the composer'}});
 const publisher='catchup-publisher';
 assert.equal((await page.request.post(url+'/api/clients/attach',{data:{clientId:publisher,kind:'web',protocolVersion:1}})).status(),200);
 const publish=async(action,args)=>{const response=await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-Client':publisher},data:{action,args}});return response.json()};
 assert.equal((await publish('session.select',{id:sessionId})).accepted,true);
 const panel=page.locator('#workspace-canvas'),refresh=()=>panel.getByRole('button',{name:'Refresh document',exact:true});
 const frame=()=>page.frameLocator('[data-canvas-view="primary"] iframe');
 const sample=async()=>({view:await view(),executed:await frame().locator('#executed').textContent(),input:await frame().getByLabel('Local unfinished input').inputValue()});
 const miss=async(id,revision,content)=>{
  held=true;const previous=dropped;
  const receipt=await publish('canvas.versions.revise',{id,sessionId,expectedRevision:revision-1,content});
  assert.equal(receipt.accepted,true,JSON.stringify(receipt));
  await expect.poll(()=>dropped).toBeGreaterThan(previous);
  assert.equal((await view()).latestVersion,revision-1,'Client must actually miss the new saved revision');
  return receipt;
 };
 const reconnect=async()=>{
  const prior=connections;held=false;const old=sockets;sockets=[];
  for(const {route,server} of old){server.close();route.close()}
  await expect.poll(()=>connections,{timeout:10000}).toBeGreaterThan(prior);
 };
 await check('Markdown Latest catches first and adjacent missed updates via Refresh',async()=>{
  const id=(await action('canvas.show',{kind:'markdown',title:'Missed Markdown',content:'# Plain v1'})).result.id;
  for(const revision of [2,3]){
   await miss(id,revision,'# Plain v'+revision);
   await expect(panel.getByRole('heading',{name:'Plain v'+(revision-1),exact:true})).toBeVisible();
   await refresh().click();await expect(panel.getByRole('heading',{name:'Plain v'+revision,exact:true})).toBeVisible();
   assert.equal((await view()).versions.length,revision);assert.equal((await view()).selectedVersion,null);
   await reconnect();
  }
  await capture('markdown-caught-up');return {versions:(await view()).versions.map(v=>v.version)};
 });
 await check('Pinned HTML discovers missed v2 without executing it or replacing input',async row=>{
  held=false;const id=(await action('canvas.show',{kind:'html',title:'Pinned executable',content:html(1)})).result.id;
  await action('canvas.select',{id,version:1});await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep pinned input');const before=await sample();
  await miss(id,2,html(2));await refresh().click();await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  const after=await sample();row.observed={before,after,dropped};
  assert.equal(after.executed,before.executed);assert.equal(after.input,before.input);assert.equal(after.view.selectedVersion,1);
  assert.deepEqual(after.view.versions.map(v=>v.version),[1,2]);await reconnect();await capture('pinned-retained');return row.observed;
 });
 await check('Latest HTML Refresh discovers v2 without executing new code or losing input',async row=>{
  held=false;const id=(await action('canvas.show',{kind:'html',title:'Latest executable Refresh',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep latest input');const before=await sample();
  await miss(id,2,html(2));await refresh().click();await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  await frame().locator('#executed').waitFor();const after=await sample();row.observed={before,after,dropped};
  // A generation/DOM token and actual field value discriminate discovery from remount.
  assert.equal(after.executed,before.executed,'Refresh must not execute the new saved HTML definition');
  assert.equal(after.input,before.input,'Refresh must not discard local HTML input');
  assert.equal(after.view.selectedVersion,null);assert.deepEqual(after.view.versions.map(v=>v.version),[1,2]);
  // Composition seam: Latest intent is not the displayed executable definition.
  // File details and its fresh download metadata must use the running v1, while
  // v2 remains available only through the existing explicit Load control.
  assert.equal(after.view.runningVersion,1);assert.equal(after.view.latestVersion,2);
  const metadata=(await action('canvas.versions.inspect',{id,sessionId,version:after.view.runningVersion})).result;
  assert.equal(metadata.version,1);assert.equal(metadata.revision,2);assert.equal(metadata.source,undefined);
  const clientId=(await state()).client.id;
  assert.equal(typeof clientId,'string');assert.ok(clientId);
  // SavedFileDownload uses the canonical clientUrl(): preserve the exact
  // version URL AND its current client binding, not a query-stripped match.
  const attachmentUrl=new URL(metadata.download.url,url);attachmentUrl.searchParams.set('clientId',clientId);
  const seam=row.observed.files={metadata,requests:[],attachmentRequests:[],downloadEvents:0,
   clientId,attachmentUrl:attachmentUrl.pathname+attachmentUrl.search,
   before:after,after:null,download:null,listenerRemoved:false};
  const retainedFrame=await panel.locator('[data-canvas-view="primary"] iframe').elementHandle();
  assert.ok(retainedFrame);
  const commandStart=report.actions.length;
  const observe=request=>{
   const requestUrl=new URL(request.url());
   if(request.method()==='POST'&&requestUrl.pathname==='/api/actions'){
    const body=unwrap(request.postDataJSON());
    if(body.action==='canvas.versions.inspect'&&body.args.id===id)seam.requests.push(body.args);
   }
   if(requestUrl.pathname===`/api/canvas/${id}/download`)seam.attachmentRequests.push({method:request.method(),url:requestUrl.pathname+requestUrl.search});
  };
  let timer,receive,expire;
  const arrived=new Promise(resolve=>{receive=download=>{seam.downloadEvents++;resolve(download)};expire=()=>resolve(null)});
  page.on('request',observe);page.on('download',receive);
  try{
   await context.grantPermissions(['clipboard-read','clipboard-write']);
   await panel.getByRole('button',{name:'Copy canvas source',exact:true}).click();
   await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(html(1));
   await panel.getByRole('button',{name:'Canvas details',exact:true}).click();
   const details=panel.locator('.a-canvas-inspector .a-canvas-file-path');
   await details.getByText('File details',{exact:true}).click();
   await expect(details.getByText('Saved version 1',{exact:true})).toBeVisible();
   assert.equal(seam.downloadEvents,0,'Copy/disclosure must not start a download');
   timer=setTimeout(expire,10000);
   await details.getByRole('button',{name:'Download saved file',exact:true}).click();
   const download=await arrived;assert.ok(download,'Running-version download did not arrive');
   assert.equal(await download.failure(),null);assert.equal(download.suggestedFilename(),metadata.download.filename);
   const filename='retained-running-v1.html';await download.saveAs(join(output,filename));
   const bytes=await readFile(join(output,filename));assert.deepEqual(bytes,Buffer.from(html(1)));
   seam.download={file:filename,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
   assert.equal(seam.downloadEvents,1);
   assert.ok(seam.requests.length>=2,'Disclosure and Download must each inspect exact metadata');
   assert.ok(seam.requests.every(args=>args.id===id&&args.version===1&&args.sessionId===sessionId&&!args.includeSource));
   assert.deepEqual(seam.attachmentRequests,[{method:'GET',url:seam.attachmentUrl}]);
   await panel.getByRole('button',{name:'Close canvas details',exact:true}).click();
   seam.after=await sample();assert.equal(seam.after.executed,after.executed);assert.equal(seam.after.input,after.input);
   assert.deepEqual(target(seam.after.view),target(after.view));
   assert.equal(seam.after.view.runningVersion,1);assert.equal(seam.after.view.latestVersion,2);
   assert.equal(seam.after.view.selectedVersion,null);
   seam.sameIframe=await retainedFrame.evaluate(node=>node.isConnected&&node===document.querySelector('[data-canvas-view="primary"] iframe'));
   assert.equal(seam.sameIframe,true);
   const current=await state();assert.equal(current.selectedSessionId,sessionId);assert.equal(current.view.draft,'Catchup must retain the composer');
   const newer=await page.request.get(url+`/api/canvas/${id}/source?version=2`);
   assert.equal(newer.status(),200);assert.equal(await newer.text(),html(2),'Inspection/download must not replace the newer saved source');
   seam.actions=report.actions.slice(commandStart);
   assert.ok(!seam.actions.some(name=>['canvas.select','canvas.show','canvas.views.loadVersion','canvas.versions.revise','canvas.versions.restore'].includes(name)));
  }finally{clearTimeout(timer);page.off('request',observe);page.off('download',receive);expire();seam.listenerRemoved=true;await retainedFrame.dispose()}
  return row.observed;
 });
 await reconnect();
 await check('Latest HTML reconnect discovers v2 without executing new code or losing input',async row=>{
  const id=(await action('canvas.show',{kind:'html',title:'Latest executable reconnect',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep reconnect input');const before=await sample();
  await miss(id,2,html(2));await reconnect();await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  await frame().locator('#executed').waitFor();const after=await sample();row.observed={before,after,connections};
  assert.equal(after.executed,before.executed,'Transport recovery must not execute a new HTML definition');
  assert.equal(after.input,before.input,'Transport recovery must not discard local HTML input');return row.observed;
 });
 for(const kind of ['html','babylon'])await check(kind+' retains adjacent versions, exact explicit Load, dirty refusal and narrow controls',async row=>{
  held=false;await page.setViewportSize({width:1500,height:950});
  const id=(await action('canvas.show',{kind,title:kind+' adoption',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep until explicit Load');
  const original=await sample();
  await miss(id,2,html(2));await refresh().click();
  await expect.poll(async()=>(await view()).latestVersion).toBe(2);await reconnect();
  assert.equal((await sample()).executed,original.executed);assert.equal((await sample()).input,original.input);
  await expect(panel.locator('.a-canvas-freshness')).toContainText('Viewing v1');
  await expect(panel.locator('.a-canvas-freshness')).toContainText('v2 available · preview retained');
  const load=version=>panel.getByRole('button',{name:`Load v${version} — resets preview and runs its code`,exact:true});
  await load(2).focus();await expect(load(2)).toBeFocused();
  let release,loadHeld=false;const gate=new Promise(resolve=>{release=resolve});
  const hold=async route=>{if(unwrap(route.request().postDataJSON()).action==='canvas.views.loadVersion'){loadHeld=true;await gate}await route.continue()};
  await page.route('**/api/actions',hold);
  try{
   await page.keyboard.press('Enter');await expect.poll(()=>loadHeld).toBe(true);
   assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:2,content:html(3)})).accepted,true);
   await expect.poll(async()=>(await view()).latestVersion).toBe(3);
   assert.equal((await sample()).executed,original.executed);assert.equal((await sample()).input,original.input);
   release();await frame().getByRole('heading',{name:'Executable version 2'}).waitFor();
   await expect.poll(async()=>(await view()).runningVersion).toBe(2);
  }finally{release();await page.unroute('**/api/actions',hold)}
  const loaded=await sample();assert.match(loaded.executed,/^executed-v2-/);assert.notEqual(loaded.executed,original.executed);
  assert.equal(loaded.input,'');assert.equal(loaded.view.selectedVersion,null);assert.equal(loaded.view.latestVersion,3);
  assert.deepEqual(loaded.view.versions.map(v=>v.version),[1,2,3]);
  await frame().getByLabel('Local unfinished input').fill('Dirty v2 input');
  await action('canvas.views.dirty',{...target(await view()),dirty:true});const dirty=await sample();
  await load(3).click();await expect(panel.getByRole('alert')).toContainText(/edit/i);
  assert.equal((await sample()).executed,dirty.executed);assert.equal((await sample()).input,dirty.input);
  assert.equal((await view()).dirty,true);
  await action('canvas.views.dirty',{...target(await view()),dirty:false});
  const narrow=[],toolbar=panel.locator('.a-canvas-artifact-toolbar');
  row.observed={original,loaded,dirty,narrow};
  if(kind==='html'){
   // Real completion feedback must coexist with the retained-preview Load row.
   // This 214-byte fixture is separate from the 426-byte Files native journey.
   let timer,receive,expire;
   const arrived=new Promise(resolve=>{receive=resolve;expire=()=>resolve(null);timer=setTimeout(expire,10000)});
   page.on('download',receive);
   try{
    await toolbar.getByRole('button',{name:'Download canvas source',exact:true}).click();
    const download=await arrived;assert.ok(download,'Narrow-layout setup download did not arrive');
    assert.equal(await download.failure(),null);
    const file=join(output,'narrow-running-v2.html');await download.saveAs(file);
    const bytes=await readFile(file);assert.deepEqual(bytes,Buffer.from(html(2)));
    row.observed.feedbackDownload={file:'narrow-running-v2.html',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
    await expect(toolbar.locator('.a-saved-file-download>[role="status"]')).toHaveText('Download started. Check your browser’s downloads.');
    const after=await sample();assert.equal(after.executed,dirty.executed);assert.equal(after.input,dirty.input);
    assert.deepEqual(target(after.view),target(dirty.view));assert.equal(after.view.runningVersion,2);
   }finally{clearTimeout(timer);page.off('download',receive);expire()}
  }
  for(const width of [390,320]){
   await page.setViewportSize({width,height:780});
   const box=await load(3).evaluate(el=>{const r=el.getBoundingClientRect();return {width:r.width,height:r.height,inside:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}});
   const layout=await toolbar.evaluate(bar=>{
    const rect=el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
    const inspect=el=>{
     const r=rect(el),style=getComputedStyle(el),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
     return {...r,tag:el.tagName,visible:el.checkVisibility({checkVisibilityCSS:true}),inert:!!el.closest('[inert]'),
      hit:el.contains(hit),nativeHit:hit===el,clipped:el.scrollWidth>el.clientWidth+1||el.scrollHeight>el.clientHeight+1,
      gridRow:style.gridRow,gridColumn:style.gridColumn,display:style.display};
    };
    const controls=Object.fromEntries([
     ['version','[aria-label="Choose artifact version"]'],['refresh','.a-canvas-refresh'],
     ['copy','[aria-label="Copy canvas source"]'],['download','.a-saved-file-download>button'],
     ['details','.a-canvas-details'],['load','.a-canvas-load-version']
    ].map(([name,selector])=>[name,inspect(bar.querySelector(selector))]));
    const freshness=bar.querySelector('.a-canvas-freshness'),status=bar.querySelector('.a-saved-file-download>[role="status"]');
    const r=rect(bar),style=getComputedStyle(bar),freshStyle=getComputedStyle(freshness);
    return {toolbar:r,contentLeft:r.left+parseFloat(style.borderLeftWidth)+parseFloat(style.paddingLeft),
     contentRight:r.right-parseFloat(style.borderRightWidth)-parseFloat(style.paddingRight),
     viewport:{width:innerWidth,height:innerHeight},overflow:bar.scrollWidth>bar.clientWidth+1,controls,
     freshness:{...inspect(freshness),paddingLeft:parseFloat(freshStyle.paddingLeft),paddingRight:parseFloat(freshStyle.paddingRight)},
     feedback:status.textContent?inspect(status):null,
     decorationPointerEvents:[...bar.querySelectorAll('.a-saved-file-download>button *')].map(el=>getComputedStyle(el).pointerEvents)};
   });
   narrow.push({width,box,layout}); // Keep the measurement even if an assertion fails.
   assert.ok(box.width>=44&&box.height>=44&&box.inside&&box.hit,JSON.stringify(box));
   assert.equal(layout.overflow,false);
   assert.ok(layout.toolbar.left>=0&&layout.toolbar.right<=layout.viewport.width,'Toolbar must fit the narrow viewport');
   for(const [name,r] of Object.entries(layout.controls)){
    assert.equal(r.tag,'BUTTON',name);assert.ok(r.width>=44&&r.height>=44,name);
    assert.ok(r.visible&&!r.inert&&r.hit&&!r.clipped,name);
    assert.ok(r.left>=layout.contentLeft-1&&r.right<=layout.contentRight+1&&r.top>=0&&r.bottom<=layout.viewport.height,name);
   }
   assert.equal(layout.controls.download.nativeHit,true);
   assert.ok(layout.decorationPointerEvents.length>0&&layout.decorationPointerEvents.every(value=>value==='none'));
   const {version,refresh:refreshBox,copy,download,details,load:loadBox}=layout.controls;
   assert.ok(Math.abs(version.top-refreshBox.top)<=1&&version.right<=refreshBox.left);
   assert.ok(copy.top>=Math.max(version.bottom,refreshBox.bottom));
   assert.ok(Math.abs(copy.top-download.top)<=1&&Math.abs(copy.top-details.top)<=1);
   assert.ok(copy.right<=download.left&&download.right<=details.left);
   const fresh=layout.freshness;
   assert.ok(fresh.visible&&!fresh.inert&&!fresh.clipped&&fresh.top>=Math.max(copy.bottom,download.bottom,details.bottom));
   assert.ok(Math.abs(fresh.left-layout.contentLeft)<=1&&Math.abs(fresh.right-layout.contentRight)<=1,'Freshness must span the toolbar');
   assert.ok(Math.abs(loadBox.left-(fresh.left+fresh.paddingLeft))<=1&&Math.abs(loadBox.right-(fresh.right-fresh.paddingRight))<=1,'Load must use the full freshness content width');
   if(kind==='html'){
    const feedback=layout.feedback;assert.ok(feedback?.visible&&!feedback.inert&&!feedback.clipped);
    assert.ok(feedback.top>=fresh.bottom&&feedback.bottom<=layout.toolbar.bottom&&feedback.bottom<=layout.viewport.height,'Download feedback must not overlap freshness/Load');
    assert.ok(Math.abs(feedback.left-layout.contentLeft)<=1&&Math.abs(feedback.right-layout.contentRight)<=1,'Download feedback must own a full row');
    assert.notEqual(feedback.gridRow,fresh.gridRow);
   }
   await capture(kind+'-retained-'+width);
  }
  await load(3).tap();await frame().getByRole('heading',{name:'Executable version 3'}).waitFor();
  const third=await sample();assert.match(third.executed,/^executed-v3-/);
  await refresh().tap();await expect(panel.locator('.a-canvas-freshness')).toContainText('Checked');
  assert.equal((await sample()).executed,third.executed,'Same-version Refresh must not run code twice');
  await page.setViewportSize({width:1500,height:950});
  await panel.getByRole('button',{name:'Choose artifact version',exact:true}).click();
  await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('1');
  await page.getByRole('button',{name:'Keep current preview',exact:true}).click();
  assert.equal((await sample()).executed,third.executed);
  await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('1');
  await page.getByRole('button',{name:'Load v1 — resets preview and runs its code',exact:true}).click();
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
  const pinned=await sample();assert.equal(pinned.view.selectedVersion,1);
  await action('canvas.select',{id,version:1});
  assert.equal((await sample()).executed,pinned.executed,'Same-version reselection does not rerun executable content');
  assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:3,content:html(4)})).accepted,true);
  await expect.poll(async()=>(await view()).latestVersion).toBe(4);
  await refresh().click();assert.equal((await sample()).executed,pinned.executed);
  const source=(await action('canvas.versions.inspect',{id,version:2,includeSource:true})).result.source.content;
  assert.equal(source,html(2));
  const download=await page.request.get(url+`/api/canvas/${id}/download?version=1`);
  assert.equal(download.status(),200);assert.ok((await download.text()).includes('Executable version 1'));
  const fresh=await publish('canvas.select',{id});assert.equal(fresh.accepted,true);
  await page.reload();await showFixtureConversation(page,{navPinned:false});
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  assert.equal((await view()).selectedVersion,1,'Cold page retains explicit historical selection, not JS memory');
  assert.notEqual((await sample()).executed,pinned.executed);
  row.observed={...row.observed,original,loaded,third,pinned,versions:(await view()).versions.map(v=>v.version)};
  await capture(kind+'-pinned-retained');return row.observed;
 });
 for(const mode of ['accepted-toolbar','accepted-details','unknown','proxy-html','malformed-details','refused'])await check('Load feedback: '+mode,async row=>{
  const details=mode.endsWith('details'),unreadable=mode==='proxy-html'||mode==='malformed-details';
  held=false;await page.setViewportSize({width:mode.startsWith('accepted')||details?390:1500,height:950});
  const id=(await action('canvas.show',{kind:'html',title:'Load feedback '+mode,content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep unless explicit Load is accepted');
  assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:1,content:html(2)})).accepted,true);
  await expect.poll(async()=>(await view()).latestVersion).toBe(2);
  if(mode==='refused')await action('canvas.views.dirty',{...target(await view()),dirty:true});
  const before=await sample();let receipt,loads=0,reads=0;
  const intercept=async route=>{
   if(unwrap(route.request().postDataJSON()).action!=='canvas.views.loadVersion')return route.continue();
   loads++;const response=await route.fetch();receipt=await response.json();
   if(mode==='unknown')return route.abort('failed');
   if(unreadable)return route.fulfill({status:mode==='proxy-html'?502:200,contentType:mode==='proxy-html'?'text/html':'application/json',body:mode==='proxy-html'?'<html>Proxy acknowledgement unavailable</html>':'{"accepted":'});
   await route.fulfill({response});
  };
  const failRead=async route=>{reads++;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic readback unavailable'})})};
  await page.route('**/api/actions',intercept);
  try{
   if(details){
    await panel.getByRole('button',{name:'Choose artifact version',exact:true}).click();
    await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('2');
   }
   // Opening Details is itself an asynchronous state action. Drain it before
   // withholding frames so its recovery cannot race this Load's readback.
   await action('canvas.views.inspect');
   await page.route('**/api/state',failRead);
   held=true;
   const load=details
    ?page.getByRole('group',{name:'Confirm saved preview version'}).getByRole('button',{name:'Load v2 — resets preview and runs its code',exact:true})
    :panel.getByRole('button',{name:'Load v2 — resets preview and runs its code',exact:true});
   if(unreadable){await load.focus();await page.keyboard.press('Enter')}else await load.click();
   await expect.poll(()=>!!receipt).toBe(true);
   row.observed={before,receipt};
   if(mode==='refused'){
    assert.equal(receipt.accepted,false);
    await expect(panel.getByRole('alert')).toContainText(/edit/i);
    const after=await sample();assert.equal(after.executed,before.executed);assert.equal(after.input,before.input);
    assert.equal(after.view.generation,before.view.generation);
   }else{
    assert.equal(receipt.accepted,true);
    assert.equal(receipt.result.runningVersion,2);assert.ok(receipt.result.generation>before.view.generation);
    if(mode.startsWith('accepted')){
     assert.equal(receipt.state,undefined,'Exercise actual delta receipt followed by a failed read, not a fabricated receipt');
     await expect.poll(()=>reads).toBeGreaterThan(0);
     const feedback=details?page.locator('#canvas-options').getByRole('alert'):panel.getByRole('alert');
     await expect(feedback).toContainText('Load v2 was accepted');
     if(details){
      await expect(feedback).toContainText('Close Canvas details to use Refresh');
      await expect(page.getByRole('combobox',{name:'Artifact version',exact:true})).toBeDisabled();
      assert.equal(await feedback.evaluate(el=>!!el.closest('[inert],[aria-hidden="true"]')),false,'Details feedback is accessible in the active narrow sheet');
     }
    }else await expect(details?page.locator('#canvas-options').getByRole('alert'):panel.getByRole('alert')).toContainText('completion is unconfirmed');
    await expect(panel.getByRole('alert')).not.toContainText('current preview is retained');
    await expect(panel.locator('.a-canvas-freshness')).not.toContainText('preview retained');
    const observedBeforeRefresh=await sample();
    assert.equal(observedBeforeRefresh.executed,before.executed);assert.equal(observedBeforeRefresh.input,before.input);
    if(!details){
     await expect(load).toHaveAttribute('aria-disabled','true');await load.click({force:true});
    }else await expect(page.getByRole('group',{name:'Confirm saved preview version'})).toHaveCount(0);
    assert.equal(loads,1,'No automatic or repeated Load while readback is unresolved');
   }
   await capture('load-feedback-'+mode);
   if(unreadable){
    // A failed explicit read cannot turn an unreadable mutation into refusal.
    if(!details){
     await refresh().click();
     await expect(panel.locator('.a-canvas-freshness')).toContainText('Could not refresh');
     await expect(panel.locator('.a-canvas-freshness')).not.toContainText('preview retained');
     assert.equal(loads,1);
    }
    await page.unroute('**/api/state',failRead);
    // Close is a separate state action. Do not inject a second unrelated
    // failure into its confirmation while testing recovery of the Load.
    if(details){
     await page.getByRole('button',{name:'Close canvas details',exact:true}).tap();
     await action('canvas.views.inspect');
    }
    await refresh().click();
    await expect.poll(async()=>(await view()).runningVersion).toBe(2);
    const second=await sample();
    assert.equal(second.view.generation,receipt.result.generation);
    assert.equal(second.view.resourceRevision,receipt.result.resourceRevision);assert.equal(loads,1);
    await page.route('**/api/state',failRead);
   }
  }catch(error){
   row.failureSurface=await page.evaluate(()=>({inspector:document.querySelector('#canvas-options')?.outerHTML,freshness:document.querySelector('.a-canvas-freshness')?.outerHTML,view:window.amplifier.getState().canvasWorkspace,expanded:window.amplifier.getState().view.canvasControlsExpanded}));
   await capture('before-cleanup-'+mode);throw error;
  }finally{
   await page.unroute('**/api/actions',intercept);await page.unroute('**/api/state',failRead);held=false;
   await reconnect();await action('canvas.views.inspect');
   if(details&&!unreadable)await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
  }
  if(mode==='refused')await action('canvas.views.dirty',{...target(await view()),dirty:false});
  else{
   await expect.poll(async()=>(await view()).runningVersion).toBe(2);
   const after=await sample();assert.match(after.executed,/^executed-v2-/);
   assert.equal(after.view.resourceRevision,receipt.result.resourceRevision);
   assert.equal(after.view.generation,receipt.result.generation);assert.equal(after.input,'');
   row.observed.after=after;
  }
  assert.equal(loads,1);
  if(unreadable){
   const second=await sample();await frame().getByLabel('Local unfinished input').fill('Keep v2 until explicit next Load');
   assert.equal((await publish('canvas.versions.revise',{id,sessionId,expectedRevision:2,content:html(3)})).accepted,true);
   await expect.poll(async()=>(await view()).latestVersion).toBe(3);
   assert.equal((await sample()).executed,second.executed);
   assert.equal((await sample()).input,'Keep v2 until explicit next Load');
   assert.equal((await view()).selectedVersion,details?2:null);
   if(details){
    await panel.getByRole('button',{name:'Choose artifact version',exact:true}).tap();
    await page.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('3');
   }
   const next=details
    ?page.getByRole('group',{name:'Confirm saved preview version'}).getByRole('button',{name:'Load v3 — resets preview and runs its code',exact:true})
    :panel.getByRole('button',{name:'Load v3 — resets preview and runs its code',exact:true});
   await next.tap();await frame().getByRole('heading',{name:'Executable version 3'}).waitFor();
   assert.equal((await view()).selectedVersion,details?3:null);
   assert.deepEqual((await view()).versions.map(v=>v.version),[1,2,3]);
   row.observed.adjacent=await sample();
  }
  row.observed.loads=loads;row.observed.failedReads=reads;return row.observed;
 });
 await check('Declared dirty view blocks publication; same-definition Refresh retains it',async row=>{
  const id=(await action('canvas.show',{kind:'html',title:'Declared dirty executable',content:html(1)})).result.id;
  await frame().getByRole('heading',{name:'Executable version 1'}).waitFor();
  await frame().getByLabel('Local unfinished input').fill('Keep declared dirty input');
  await action('canvas.views.dirty',{...target(await view()),dirty:true});const before=await sample();
  const rejected=await publish('canvas.versions.revise',{id,sessionId,expectedRevision:1,content:html(2)});
  assert.equal(rejected.accepted,false);assert.match(rejected.error,/artifact edit/);
  await refresh().click();await expect(panel.locator('.a-canvas-freshness')).toContainText('Checked');
  const after=await sample();row.observed={before,after,rejected};
  assert.equal(after.executed,before.executed);assert.equal(after.input,before.input);assert.equal(after.view.dirty,true);
  assert.deepEqual(target(after.view),target(before.view));return row.observed;
 });
 assert.equal((await state()).view.draft,'Catchup must retain the composer');assert.deepEqual(report.errors,[]);
 report.droppedFrames=dropped;report.connections=connections;report.status=report.checks.every(c=>c.status==='passed')?'passed':'failed';
}catch(error){fatal=error;report.status='failed';report.error=error.stack||String(error);if(page)try{await capture('fatal')}catch{}}
finally{
 held=false;await browser?.close();if(fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));await writeFile(join(output,'fixture.stdout.log'),stdout);await writeFile(join(output,'fixture.stderr.log'),stderr);
 console.log(JSON.stringify({output,status:report.status,checks:report.checks.map(({name,status,error,observed})=>({name,status,error,observed:observed?{beforeToken:observed.before?.executed,afterToken:observed.after?.executed,beforeInput:observed.before?.input,afterInput:observed.after?.input}:null})),error:report.error}));
}
if(fatal||report.status!=='passed')process.exitCode=1;
