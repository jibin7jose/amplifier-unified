import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python';
const directory=await mkdtemp(join(tmpdir(),'mcp-recovery-'));
const htmlPath=join(directory,'app.html'),callsPath=join(directory,'calls.jsonl'),modePath=join(directory,'mode');
const source=`import {App} from '@modelcontextprotocol/ext-apps';
const app=new App({name:'Saved recovery fixture',version:'1.0.0'},{});
const draw=result=>document.querySelector('#count').textContent=String(result.structuredContent.count);
let inputs=0,results=0,notifications=0;
app.ontoolinput=()=>document.body.dataset.inputs=String(++inputs);
app.ontoolresult=result=>{document.body.dataset.results=String(++results);draw(result)};
app.setNotificationHandler('notifications/tools/list_changed',async()=>{
 try{
  const result=await app.callServerTool({name:'counter_read',arguments:{}});
  document.querySelector('#recovered').textContent=String(result.structuredContent.count);
  document.body.dataset.recoveries=String(++notifications);
 }catch(error){document.querySelector('#error').textContent=error.message}
});
document.querySelector('#add').onclick=async()=>{try{draw(await app.callServerTool({name:'counter_add',arguments:{amount:1}}))}catch(error){document.querySelector('#error').textContent=error.message}};
window.fixtureCall=name=>app.callServerTool({name,arguments:{}});
await app.connect();document.body.dataset.booted='true';`;
const built=await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'recovery.js'},bundle:true,write:false,format:'esm'});
await writeFile(htmlPath,`<!doctype html><h1>Original saved document</h1><output id="count"></output><output id="recovered"></output><button id="add">Add one</button><input aria-label="Unfinished tool input"><p id="error"></p><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
let fixture,browser,secondBrowser,originalError,cleanupComplete=false;
async function start(port=0){
 fixture=spawn(python,[root+'tests/fixtures/canvas_restart_ui_server.py',directory,String(port)],{detached:process.platform!=='win32',stdio:['ignore','pipe','inherit'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 return new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url)}}catch{}}});
 });
}
async function stop(){
 if(!fixture)return;
 const child=fixture;fixture=null;let shutdownError,timer;
 if(child.exitCode===null&&child.signalCode===null){
  const exited=once(child,'exit');child.kill('SIGTERM');
  try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('MCP fixture did not finish graceful shutdown')),10000)})])}
  catch(error){shutdownError=error}
  finally{clearTimeout(timer)}
 }
 if(process.platform==='win32'){
  if(shutdownError)child.kill('SIGKILL');
  if(shutdownError)throw shutdownError;
  return;
 }
 // A cancelled bundle download may still write after the host exits. Retire
 // only this fixture's private group, even when parent shutdown timed out.
 const signal=kind=>{try{process.kill(-child.pid,kind);return true}catch(error){if(error.code==='ESRCH')return false;throw error}};
 const waitForGroup=async()=>{
  for(let attempt=0;attempt<100;attempt++){
   if(!signal(0))return true;
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  return false;
 };
 if(signal('SIGTERM')&&!await waitForGroup()){
  signal('SIGKILL');
  if(!await waitForGroup())throw Error('Owned MCP fixture group remained alive after forced cleanup',{cause:shutdownError});
  throw Error('Owned MCP fixture group required forced cleanup',{cause:shutdownError});
 }
 if(shutdownError)throw shutdownError;
}

const mutations=async()=>((await readFile(callsPath,'utf8')).trim().split('\n').filter(Boolean)).length;
const unwrap=raw=>raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
async function dismissFixtureSetup(page,boundary){
 // Like mcp-clients, this credential-free fixture must settle real discovery
 // before closing setup. Reloads remount the per-client setup prompt as well.
 await page.evaluate(()=>window.amplifier.dispatch('providers.list',{sessionId:window.amplifier.getState().selectedSessionId}));
 await expect.poll(()=>page.evaluate(()=>{
  const state=window.amplifier.getState(),setup=state.setup||{};
  const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
  const workspace=session?.workspace||state.settings?.workspace;
  return Boolean(setup.providersLoadedAt&&(setup.providersWorkspace===workspace||!session&&setup.providersLocation?.kind==='managed'));
 })).toBe(true);
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().setup.providers),[],'Recovery fixture has no AI connections');
 const setup=page.locator('.a-settings-experience');await expect(setup).toBeVisible();
 await expect(setup).toContainText('Connect one AI service to start.');
 const saved=page.waitForResponse(response=>{
  if(!response.url().endsWith('/api/actions')||response.request().method()!=='POST')return false;
  const body=unwrap(response.request().postDataJSON());return body?.action==='view.update'&&body.args?.patch?.panel===null;
 });
 await page.locator('.a-settings-overlay').getByRole('button',{name:/^Close (?:settings|panel)$/}).click();
 const response=await saved;assert.ok(response.ok());const receipt=await response.json();assert.equal(receipt.accepted,true,JSON.stringify(receipt));
 await expect(setup).toHaveCount(0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.panel)).toBe(null);
 console.log('MCP recovery setup boundary: '+boundary+'; empty catalog loaded, real close accepted, rendered setup absent.');
}
try{
 const url=await start();
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1450,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const operation=async(name,args)=>{
  const receipt=await action(name,args);
  if(!receipt.operationId)return receipt.result;
  for(let attempt=0;attempt<200;attempt++){
   const row=await (await page.request.get(url+'/api/smart-tools/operations/'+receipt.operationId)).json();
   if(row.status==='completed')return row.result;
   if(row.status==='failed')throw Error(row.error);
   await page.waitForTimeout(50);
  }
  throw Error('Operation did not finish: '+name);
 };
 await action('session.create');
 await dismissFixtureSetup(page,'initial client');
 await action('view.update',{patch:{canvasControlsPinned:true}});
 await operation('smartTools.configure',{id:'recovery',name:'Recovery fixture',command:python,args:[root+'tests/fixtures/canvas_recovery_mcp_server.py',htmlPath,callsPath,modePath]});
 await operation('smartTools.connect',{id:'recovery'});
 const launch=await action('smartTools.call',{id:'recovery',name:'counter_add',arguments:{amount:7}});
 await expect.poll(async()=>(await (await page.request.get(url+'/api/smart-tools/operations/'+launch.operationId)).json()).status,{timeout:15000}).toBe('completed');
 await operation('smartTools.open',{id:'recovery',tool:'counter_add',operationId:launch.operationId});
 const frame=page.frameLocator('.a-mcp-app-viewer iframe');
 await expect(frame.locator('body')).toHaveAttribute('data-booted','true');
 await expect(frame.locator('#count')).toHaveText('7');
 const snapshot=()=>page.evaluate(()=>{const state=window.amplifier.getState(),row=state.canvasArtifacts.find(row=>row.id===state.canvas.id);return {id:row.id,body:row.body,messageId:row.messageId,artifacts:state.canvasArtifacts.length,session:state.selectedSessionId,launch:state.canvas.mcp.operationId,arguments:state.canvas.mcp.toolArguments,grants:state.canvas.mcp.allowedTools}});
 const before=await snapshot();
 assert.equal(await mutations(),1);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep the unsent message');
 await action('view.update',{patch:{draft:'Keep the unsent message'}});
 await operation('smartTools.disconnect',{id:'recovery'});
 await page.reload();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep the unsent message');
 await expect(frame.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await dismissFixtureSetup(page,'disconnect reload');
 await expect(page.getByRole('status').filter({hasText:'Saved document available.'})).toBeVisible();
 await frame.getByRole('textbox',{name:'Unfinished tool input'}).fill('Keep me through reconnect');
 await frame.locator('body').evaluate(body=>body.dataset.retained='yes');
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(frame.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Keep me through reconnect');
 await expect(frame.locator('body')).toHaveAttribute('data-retained','yes');
 await expect(frame.locator('#recovered')).toHaveText('7');
 await expect(frame.locator('body')).toHaveAttribute('data-recoveries','1');
 await expect(frame.locator('body')).toHaveAttribute('data-inputs','1');
 await expect(frame.locator('body')).toHaveAttribute('data-results','1');
 assert.equal(await mutations(),1);
 assert.deepEqual(await snapshot(),before);
 // Ordinary Latest reopen and deliberately selected history are distinct.
 await page.getByRole('button',{name:'Canvas details',exact:true}).click();
 await page.getByLabel('Artifact version',{exact:true}).selectOption('1');
 await expect(page.getByRole('status').filter({hasText:'Saved version · Read only'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Open live view',exact:true})).toBeVisible();
 await frame.getByRole('button',{name:'Add one'}).click();
 await expect(frame.locator('#error')).toContainText('read-only');
 assert.equal(await mutations(),1);
 await page.getByRole('button',{name:'Open live view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(frame.locator('body')).toHaveAttribute('data-inputs','1');
 assert.equal(await mutations(),1);
 await frame.getByRole('button',{name:'Add one'}).click();
 await expect(frame.locator('#count')).toHaveText('8');
 assert.equal(await mutations(),2);
 // Replace only the server's current resource, then restart the real host.
 // Recovery must use the immutable saved source, not read today's resource.
 const savedHtml=await readFile(htmlPath,'utf8');
 await writeFile(htmlPath,savedHtml.replace('Original saved document','Updated saved document'));
 await stop();await start(Number(new URL(url).port));await page.reload();
 await expect(frame.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await expect(frame.locator('#count')).toHaveText('7');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep the unsent message');
 await dismissFixtureSetup(page,'host restart reload');
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(frame.locator('#recovered')).toHaveText('8');
 await expect(frame.locator('body')).toHaveAttribute('data-recoveries','1');
 await expect(frame.locator('body')).toHaveAttribute('data-inputs','1');
 await expect(frame.locator('body')).toHaveAttribute('data-results','1');
 assert.deepEqual(await snapshot(),before);
 assert.equal(await mutations(),2);
 // An actual changed MCP schema must stay rejected after discovery.
 await operation('smartTools.disconnect',{id:'recovery'});
 await writeFile(modePath,'changed');await page.reload();
 await expect(frame.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await dismissFixtureSetup(page,'changed schema reload');
 await page.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(page.locator('.a-mcp-status')).toContainText('action schemas changed');
 assert.equal(await mutations(),2);
 assert.equal(await frame.locator('body').getAttribute('data-recoveries'),null,'Denied schema changes do not notify the app to retry');
 assert.deepEqual(await snapshot(),before);
 await frame.getByRole('button',{name:'Add one'}).click();
 await expect(frame.locator('#error')).toContainText('action schemas changed');
 assert.equal(await mutations(),2);
 // Required review remains visible even when optional Canvas controls are closed.
 const options=page.getByRole('button',{name:'Canvas details',exact:true});
 if(await options.getAttribute('aria-expanded')==='true')await options.click();
 await expect(page.getByRole('button',{name:'Review tool changes',exact:true})).toBeVisible();
 await expect(page.locator('#canvas-options .a-mcp-contract-review')).toHaveCount(0);
 // A second independently attached client holds the original saved document.
 secondBrowser=await chromium.launch({headless:true});
 const secondContext=await secondBrowser.newContext({viewport:{width:1100,height:850},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const second=await secondContext.newPage();await second.goto(url);await showFixtureConversation(second);
 await second.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await second.evaluate(async before=>{await window.amplifier.dispatch('session.select',{id:before.session});await window.amplifier.dispatch('canvas.select',{id:before.id});},before);
 await dismissFixtureSetup(second,'second client');
 const other=second.frameLocator('.a-mcp-app-viewer iframe');
 await expect(other.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await other.getByRole('textbox',{name:'Unfinished tool input'}).fill('Other client keeps this');
 await other.locator('body').evaluate(body=>body.dataset.preserved='original-node');
 await frame.getByRole('textbox',{name:'Unfinished tool input'}).fill('Current client keeps this on contract-only approval');
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 await page.getByRole('button',{name:'Review tool changes',exact:true}).click();
 const review=page.getByRole('region',{name:'Review tool changes'});
 await expect(review).toContainText('counter_extra');
 await expect(review).toContainText('counter_add (inputs)');
 await expect(review).toContainText('Added actions — not granted');
 if(process.env.MCP_RECOVERY_SCREENSHOT)await page.screenshot({path:process.env.MCP_RECOVERY_SCREENSHOT.replace(/\.png$/,'-review.png')});
 await review.getByRole('button',{name:'Approve existing actions',exact:true}).click();
 // Successful approval moves the optional review into the closed Canvas details.
 // Read the visible connection state without waiting on that now-hidden region.
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(frame.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Current client keeps this on contract-only approval');
 assert.equal(await mutations(),2);
 const ungranted=await frame.locator('body').evaluate(async()=>{try{await window.fixtureCall('counter_extra');return 'unexpected success'}catch(error){return error.message}});
 assert.match(ungranted,/not granted/);
 // A healthy tool keeps optional review in Canvas details, leaving its view clear.
 await expect(page.getByRole('button',{name:'Review tool changes',exact:true})).toBeHidden();
 await options.click();
 await expect(page.locator('#canvas-options .a-mcp-contract-review')).toBeVisible();
 await page.getByRole('button',{name:'Review tool changes',exact:true}).click();
 await review.getByRole('checkbox',{name:/Open updated live view/}).check();
 await expect(review.getByRole('button',{name:'Approve and open updated live view',exact:true})).toBeDisabled();
 await review.getByRole('checkbox',{name:/I understand/}).check();
 await review.getByRole('button',{name:'Approve and open updated live view',exact:true}).click();
 await expect(frame.getByRole('heading',{name:'Updated saved document'})).toBeVisible();
 await expect(frame.locator('#count')).toHaveText('7');
 await expect(other.getByRole('heading',{name:'Original saved document'})).toBeVisible();
 await expect(other.locator('body')).toHaveAttribute('data-preserved','original-node');
 await expect(other.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Other client keeps this');
 await expect(second.getByRole('button',{name:'Open live view',exact:true})).toBeVisible();
 await other.getByRole('button',{name:'Add one'}).click();
 await expect(other.locator('#error')).toContainText('read-only');
 const hostDenied=await second.evaluate(async id=>{try{await window.amplifier.dispatch('smartTools.appCall',{canvasId:id,expectedRevision:1,name:'counter_add',arguments:{amount:'1'}});return 'unexpected success'}catch(error){return error.message}},before.id);
 assert.match(hostDenied,/read-only/,'Server authority also denies calls from the preserved historical frame');
 if(process.env.MCP_RECOVERY_SCREENSHOT)await second.screenshot({path:process.env.MCP_RECOVERY_SCREENSHOT.replace(/\.png$/,'-other-client.png')});
 const after=await snapshot();assert.equal(after.id,before.id);assert.equal(after.launch,before.launch);
 assert.deepEqual(after.arguments,before.arguments);assert.deepEqual([...after.grants].sort(),[...before.grants].sort());
 assert.equal(after.artifacts,before.artifacts);assert.notDeepEqual(after.body,before.body);assert.equal(await mutations(),2);
 await secondContext.close();
 await page.screenshot({path:process.env.MCP_RECOVERY_SCREENSHOT||join(directory,'mcp-recovery.png')});
 assert.deepEqual(errors,[]);
 console.log('MCP recovery browser passed: actual MCP SDK, disconnect and host restart, same artifact/source hash, historical launch receipt, explicit unchanged-contract rebind without frame/input replacement, single SDK recovery notification and safe read, explicit history/Latest selection, no replayed mutations, changed schema denied until exact review, current-HTML adoption with no launch replay, new tools ungranted, second-client saved iframe/input preserved and read-only, composer preserved.');
}catch(error){originalError=error;throw error}finally{
 try{
  await finishFixture({label:'MCP recovery fixture',close:[
   ['second browser',async()=>{await secondBrowser?.close()}],
   ['browser',async()=>{await browser?.close()}],
  ],stop:async()=>{await stop();await rm(directory,{recursive:true,force:true})}});
  cleanupComplete=true;
 }catch(error){if(originalError)console.error(error);else throw error}
}
