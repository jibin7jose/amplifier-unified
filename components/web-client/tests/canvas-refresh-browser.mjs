// Exact production assets/host; synthetic data and a separate publishing client.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks');
await mkdir(base,{recursive:true});
const output=await mkdtemp(join(base,'canvas-refresh-'));
const report={status:'running',checks:[],screenshots:[],browserErrors:[],baseline:process.env.CANVAS_REFRESH_BASELINE==='1'};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,failure,stdout='',stderr='';
fixture.stderr.on('data',chunk=>stderr+=chunk);
const capture=async name=>{await page.screenshot({path:join(output,name+'.png')});report.screenshots.push(name+'.png')};
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1500,height:950},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();page.setDefaultTimeout(8000);
 page.on('pageerror',error=>report.browserErrors.push(error.message));
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=async(name,args={})=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result.accepted,true);return result};
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await action('session.create');
 const session=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasWidth:800,canvasControlsExpanded:false,draft:'Preserve this unfinished message'}});
 const publisher='synthetic-publisher';
 assert.equal((await page.request.post(url+'/api/clients/attach',{data:{clientId:publisher,kind:'web',protocolVersion:1}})).status(),200);
 const publish=async(name,args)=>{
  const response=await page.request.post(url+'/api/actions',{headers:{'X-Amplifier-Client':publisher},data:{action:name,args}});
  const result=await response.json();assert.equal(result.accepted,true,JSON.stringify(result));return result;
 };
 await publish('session.select',{id:session});
 const panel=page.locator('#workspace-canvas');
 const document=await action('canvas.show',{kind:'markdown',title:'Revision discovery',content:'# Saved one'});
 const id=document.result.id;
 // No inspection or other foreground action between publication and these assertions.
 for(const [revision,title] of [[2,'Saved two'],[3,'Saved three']]){
  await publish('canvas.versions.revise',{id,sessionId:session,expectedRevision:revision-1,content:'# '+title});
  await expect(panel.getByRole('heading',{name:title,exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Choose artifact version',exact:true})).toContainText('v'+revision);
  assert.equal((await state()).canvasWorkspace.views.find(v=>v.viewId==='primary').versions.length,revision);
  report.checks.push('Latest discovers revision '+revision+' without chat link or inspect');
 }
 await action('canvas.select',{id,version:1});
 await publish('canvas.versions.revise',{id,sessionId:session,expectedRevision:3,content:'# Saved four'});
 await expect(panel.getByRole('heading',{name:'Saved one',exact:true})).toBeVisible();
 await expect.poll(async()=>(await state()).canvasWorkspace.views.find(v=>v.viewId==='primary').latestVersion).toBe(4);
 assert.equal((await state()).canvasWorkspace.views.find(v=>v.viewId==='primary').versions.length,4);
 assert.equal((await state()).canvas.selectedVersion,1);
 report.checks.push('Exact v1 remains selected while discovering newer saved revisions');
 await capture('historical-desktop');
 if(report.baseline){
  report.refreshPresent=await panel.getByRole('button',{name:'Refresh document',exact:true}).count();
  assert.equal(report.refreshPresent,0,'Baseline missing Refresh negative control');
 }else{
  const refresh=()=>panel.getByRole('button',{name:'Refresh document',exact:true});
  const status=()=>panel.locator('.a-canvas-freshness');
  await expect(status()).toContainText('Version 1');await expect(status()).toContainText('Version 4');
  await refresh().focus();await expect(refresh()).toBeFocused();await page.keyboard.press('Enter');
  await expect(status()).toContainText('Checked');
  await expect(panel.getByRole('heading',{name:'Saved one',exact:true})).toBeVisible();
  assert.equal((await state()).canvas.selectedVersion,1);
  await expect(refresh()).toBeFocused();
  await panel.getByRole('button',{name:'Choose artifact version',exact:true}).click();
  const versions=page.getByRole('combobox',{name:'Artifact version',exact:true});
  await expect(versions).toHaveValue('1');
  for(const revision of [1,2,3,4])await expect(versions.locator('option[value="'+revision+'"]')).toHaveCount(1);
  await versions.selectOption('latest');
  await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
  await expect(panel.getByRole('heading',{name:'Saved four',exact:true})).toBeVisible();
  await refresh().click();await expect(status()).toContainText('Checked');await expect(status()).toContainText('Latest');
  await capture('latest-desktop');
  report.checks.push('Keyboard Refresh preserves historical v1; existing selector retains all versions and returns to Latest');
  // Delay and fail the real read request. No forged successful product response.
  const unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
  let release,held=false;
  const gate=new Promise(resolve=>{release=resolve});
  const delay=async route=>{if(unwrap(route.request().postDataJSON())?.action==='canvas.views.inspect'){held=true;await gate;await route.abort('failed')}else await route.continue()};
  await page.route('**/api/actions',delay);
  try{
   await refresh().click();await expect.poll(()=>held).toBe(true);
   await expect(refresh()).toBeDisabled();await expect(status()).toContainText('Checking');await capture('loading');
   release();await expect(panel.getByRole('alert').filter({hasText:'Could not refresh'})).toBeVisible();
   await expect(refresh()).toBeEnabled();await expect(panel.getByRole('heading',{name:'Saved four',exact:true})).toBeVisible();
   await capture('failure-feedback');
  }finally{release();await page.unroute('**/api/actions',delay)}
  await refresh().click();await expect(status()).toContainText('Checked');
  await expect(panel.getByRole('alert').filter({hasText:'Could not refresh'})).toHaveCount(0);
  for(const width of [390,320]){
   await page.setViewportSize({width,height:780});
   const box=await refresh().evaluate(el=>{const r=el.getBoundingClientRect();return {width:r.width,height:r.height,inside:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}});
   assert.ok(box.width>=44&&box.height>=44&&box.inside&&box.hit,JSON.stringify(box));
   await refresh().tap();await expect(status()).toContainText('Checked');
   await capture('latest-narrow-'+width);
  }
  report.checks.push('Loading/failure/recovery are explicit; 390px/320px touch targets are unclipped and reachable');
  await page.setViewportSize({width:1500,height:950});
  // Inspect must not remount/execute existing HTML code or erase live DOM edits.
  await action('canvas.show',{kind:'html',title:'Retain interactive view',content:'<label>Unfinished input <input id="draft"></label><output id="mount"></output><script>document.getElementById("mount").textContent=crypto.randomUUID()</script>'});
  const frame=page.frameLocator('[data-canvas-view="primary"] iframe');
  await frame.getByLabel('Unfinished input').fill('Retain local HTML input');
  const mount=await frame.locator('#mount').textContent();assert.ok(mount);
  await refresh().click();await expect(status()).toContainText('Checked');
  assert.equal(await frame.locator('#mount').textContent(),mount);
  await expect(frame.getByLabel('Unfinished input')).toHaveValue('Retain local HTML input');
  report.checks.push('Refresh does not rerun HTML or discard interactive input');
  const pinned=(await action('canvas.show',{kind:'markdown',title:'Pinned before first update',content:'# Pinned first'})).result.id;
  await action('canvas.select',{id:pinned,version:1});
  for(const revision of [2,3]){
   await publish('canvas.versions.revise',{id:pinned,sessionId:session,expectedRevision:revision-1,content:'# Pinned '+revision});
   await expect(status()).toContainText('Historical · Version '+revision+' available');
   await expect(panel.getByRole('heading',{name:'Pinned first',exact:true})).toBeVisible();
   assert.equal((await state()).canvas.selectedVersion,1);
   await refresh().click();await expect(status()).toContainText('Checked');
   assert.equal((await state()).canvasWorkspace.views.find(v=>v.viewId==='primary').versions.length,revision);
  }
  await capture('pinned-first-update-desktop');
  report.checks.push('Pinned v1 discovers its first and adjacent updates without replacing its content');
  await action('canvas.select',{id:pinned});
  const composer=page.getByRole('textbox',{name:'Message Amplifier'});
  await composer.fill('Preserve this unfinished message, typed in the composer');
  await refresh().click();await expect(status()).toContainText('Checked');
  await expect(composer).toHaveText('Preserve this unfinished message, typed in the composer');
  const manifest={id:'example.refresh-editor',label:'Refresh editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
  const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"Unfinished renderer edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
  const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
  assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
  const view=() => state().then(s=>s.canvasWorkspace.views.find(v=>v.viewId==='primary'));
  const target=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]));
  await action('canvas.views.renderer',{...target(await view()),renderer:digest});
  const editor=panel.getByLabel('Unfinished renderer edit');
  await editor.fill('Retain the unsaved renderer draft');
  await expect.poll(async()=>(await view()).dirty).toBe(true);
  const editorMount=await editor.getAttribute('data-mount'),before=target(await view());
  await refresh().click();await expect(status()).toContainText('Checked');
  assert.deepEqual(target(await view()),before);assert.equal((await view()).dirty,true);
  assert.equal(await editor.getAttribute('data-mount'),editorMount);
  await expect(editor).toHaveValue('Retain the unsaved renderer draft');
  await capture('dirty-renderer-refresh');
  report.checks.push('Refresh retains a declared dirty renderer, generation, mount identity and unfinished draft');
 }
 if(report.baseline)assert.equal((await state()).view.draft,'Preserve this unfinished message');
 else await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Preserve this unfinished message, typed in the composer');
 const history=(await action('canvas.versions.inspect',{id,version:2,includeSource:true})).result;
 assert.equal(history.source.content,'# Saved two');
 assert.deepEqual(report.browserErrors,[]);
 report.status='passed';
}catch(error){failure=error;report.status='failed';report.error=error.stack||String(error);if(page)try{await capture('failed')}catch{}}
finally{
 await browser?.close();
 if(fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));
 await writeFile(join(output,'fixture.stdout.log'),stdout);await writeFile(join(output,'fixture.stderr.log'),stderr);
 console.log(JSON.stringify({output,...report}));
}
if(failure)throw failure;
