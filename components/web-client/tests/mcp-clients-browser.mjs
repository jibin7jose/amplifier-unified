import {showFixtureConversation} from './browser-chat-pages.mjs';
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
app.ontoolresult=draw;
document.querySelector('#add').onclick=async()=>{try{draw(await app.callServerTool({name:'counter_add',arguments:{amount:1}}))}catch(error){document.querySelector('#error').textContent=error.message}};
await app.connect();document.body.dataset.booted='true';`;
const built=await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:'recovery.js'},bundle:true,write:false,format:'esm'});
await writeFile(htmlPath,`<!doctype html><h1>Original saved document</h1><output id="count"></output><button id="add">Add one</button><input aria-label="Unfinished tool input"><p id="error"></p><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script>`);
let fixture,browser;
async function start(port=0){
 fixture=spawn(python,[root+'tests/fixtures/canvas_restart_ui_server.py',directory,String(port)],{stdio:['ignore','pipe','inherit'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 return new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url)}}catch{}}});
 });
}
async function stop(){if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');await exited}fixture=null}
const mutations=async()=>((await readFile(callsPath,'utf8')).trim().split('\n').filter(Boolean)).length;
const unwrap=raw=>raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
async function dismissFixtureSetup(page){
 // This credential-free fixture discovers an empty provider catalog. Finish
 // that real discovery before dismissing its ordinary setup UI, once per client.
 await page.evaluate(()=>window.amplifier.dispatch('providers.list',{sessionId:window.amplifier.getState().selectedSessionId}));
 await expect.poll(()=>page.evaluate(()=>{
  const state=window.amplifier.getState(),setup=state.setup||{};
  const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
  const workspace=session?.workspace||state.settings?.workspace;
  return Boolean(setup.providersLoadedAt&&(setup.providersWorkspace===workspace||!session&&setup.providersLocation?.kind==='managed'));
 })).toBe(true);
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
 await dismissFixtureSetup(page);
 await action('view.update',{patch:{canvasControlsPinned:true}});
 await operation('smartTools.configure',{id:'recovery',name:'Recovery fixture',command:python,args:[root+'tests/fixtures/canvas_recovery_mcp_server.py',htmlPath,callsPath,modePath]});
 await operation('smartTools.connect',{id:'recovery'});
 const launch=await action('smartTools.call',{id:'recovery',name:'counter_add',arguments:{amount:7}});
 // The compact browser frame intentionally omits the operation collection.
 // Wait on this exact durable receipt before opening its saved result.
 await expect.poll(async()=>{
  const response=await page.request.get(url+'/api/smart-tools/operations/'+launch.operationId);
  assert.ok(response.ok());const row=await response.json();return {id:row.id,status:row.status};
 },{timeout:15000}).toEqual({id:launch.operationId,status:'completed'});
 await operation('smartTools.open',{id:'recovery',tool:'counter_add',operationId:launch.operationId});
 const frame=page.frameLocator('.a-mcp-app-viewer iframe');
 await expect(frame.locator('body')).toHaveAttribute('data-booted','true');
 await expect(frame.locator('#count')).toHaveText('7');
 const snapshot=()=>page.evaluate(()=>{const state=window.amplifier.getState(),row=state.canvasArtifacts.find(row=>row.id===state.canvas.id);return {id:row.id,body:row.body,messageId:row.messageId,artifacts:state.canvasArtifacts.length,session:state.selectedSessionId,launch:state.canvas.mcp.operationId,arguments:state.canvas.mcp.toolArguments,grants:state.canvas.mcp.allowedTools}});
 const before=await snapshot();
 assert.equal(await mutations(),1);
 const mobileContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const mobile=await mobileContext.newPage();
 mobile.on('pageerror',error=>errors.push(error.message));
 await mobile.goto(url);await showFixtureConversation(mobile);await mobile.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const mobileAction=(name,args={})=>mobile.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await mobileAction('session.select',{id:before.session});
 await mobileAction('canvas.select',{id:before.id});
 await dismissFixtureSetup(mobile);
 const phoneFrame=mobile.frameLocator('.a-mcp-app-viewer iframe');
 await expect(phoneFrame.locator('body')).toHaveAttribute('data-booted','true');
 await expect(phoneFrame.locator('#count')).toHaveText('7');
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 assert.equal(await mutations(),1);
 await phoneFrame.getByRole('textbox',{name:'Unfinished tool input'}).fill('Keep phone input');
 // Both viewers stay mounted while shared server authority changes.
 await operation('smartTools.disconnect',{id:'recovery'});
 await mobile.waitForFunction(()=>window.amplifier.getState().smartTools.servers.find(row=>row.id==='recovery')?.status==='disconnected');
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 await expect(mobile.locator('.a-mcp-status')).toContainText('Reconnect this tool');
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 await expect(phoneFrame.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Keep phone input');
 await phoneFrame.locator('body').evaluate(body=>body.dataset.retained='yes');
 await expect(mobile.locator('.a-settings-experience')).toHaveCount(0);
 await mobile.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(phoneFrame.locator('body')).toHaveAttribute('data-retained','yes');
 await expect(phoneFrame.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Keep phone input');
 assert.equal(await mutations(),1);
 // Foreground return refreshes status, not the frame or its launch mutation.
 let statusReads=0;
 mobile.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/status'))statusReads++});
 const visibility=async value=>mobile.evaluate(value=>{Object.defineProperty(document,'visibilityState',{configurable:true,value});document.dispatchEvent(new Event('visibilitychange'))},value);
 await visibility('hidden');
 const priorReads=statusReads;
 await visibility('visible');
 await expect.poll(()=>statusReads).toBeGreaterThan(priorReads);
 await expect(phoneFrame.locator('body')).toHaveAttribute('data-retained','yes');
 assert.equal(await mutations(),1);
 // An old successful check must not overwrite a newer disconnected result.
 let release,arrived;
 const held=new Promise(resolve=>{arrived=resolve}),gate=new Promise(resolve=>{release=resolve});
 let hold=true;
 await mobile.route('**/api/canvas/*/status?*',async route=>{
  if(!hold)return route.continue();hold=false;
  const response=await route.fetch();arrived();await gate;await route.fulfill({response});
 });
 await visibility('hidden');await visibility('visible');await held;
 await operation('smartTools.disconnect',{id:'recovery'});
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 release();await mobile.waitForTimeout(100);
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 await expect(mobile.locator('.a-mcp-status')).toContainText('Reconnect this tool');
 await mobile.unroute('**/api/canvas/*/status?*');
 await expect(mobile.locator('.a-settings-experience')).toHaveCount(0);
 await mobile.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(phoneFrame.getByRole('textbox',{name:'Unfinished tool input'})).toHaveValue('Keep phone input');
 assert.equal(await mutations(),1);
 // A completed tool call can arrive after another client's disconnect.
 let releaseCall,callArrived;
 const completedCall=new Promise(resolve=>{callArrived=resolve}),callGate=new Promise(resolve=>{releaseCall=resolve});
 await mobile.route('**/api/canvas/*/tools/call?*',async route=>{
  const response=await route.fetch();callArrived();await callGate;await route.fulfill({response});
 });
 await phoneFrame.getByRole('button',{name:'Add one'}).click();await completedCall;
 await operation('smartTools.disconnect',{id:'recovery'});
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 releaseCall();await expect(phoneFrame.locator('#count')).toHaveText('8');
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','error');
 await expect(mobile.locator('.a-mcp-status')).toContainText('Reconnect this tool');
 await mobile.unroute('**/api/canvas/*/tools/call?*');
 await expect(mobile.locator('.a-settings-experience')).toHaveCount(0);
 await mobile.getByRole('button',{name:'Reconnect tool view',exact:true}).click();
 await expect(mobile.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 await expect(page.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 assert.equal(await mutations(),2);
 await frame.getByRole('button',{name:'Add one'}).click();
 await expect(frame.locator('#count')).toHaveText('9');
 assert.equal(await mutations(),3);
 await phoneFrame.getByRole('button',{name:'Add one'}).click();
 await expect(phoneFrame.locator('#count')).toHaveText('10');
 assert.equal(await mutations(),4);
 // Initial source admission must use the newest check, too. A late
 // obsolete source-unavailable response must not block the saved iframe.
 for(const initialFailure of ['unavailable','http-error']){
 const thirdContext=await browser.newContext({viewport:{width:900,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const third=await thirdContext.newPage();third.on('pageerror',error=>errors.push(error.message));
 let releaseInitial,initialArrived,currentArrived,initial=true;
 const oldInitial=new Promise(resolve=>{initialArrived=resolve}),initialGate=new Promise(resolve=>{releaseInitial=resolve});
 const freshInitial=new Promise(resolve=>{currentArrived=resolve});
 await third.route('**/api/canvas/*/status?*',async route=>{
  const response=await route.fetch();
  if(!initial){await route.fulfill({response});currentArrived();return}
  initial=false;initialArrived();await initialGate;
  if(initialFailure==='http-error')await route.fulfill({status:503,json:{error:'Obsolete initial failure'}});
  else await route.fulfill({response,json:{...(await response.json()),source:'unavailable',status:'source_unavailable',canReconnect:false,message:'Obsolete initial unavailable'}});
 });
 await third.goto(url);await third.waitForFunction(()=>window.amplifier?.getState());
 await third.evaluate(([sid,cid])=>window.amplifier.dispatch('session.select',{id:sid}).then(()=>window.amplifier.dispatch('canvas.select',{id:cid})),[before.session,before.id]);
 await dismissFixtureSetup(third);
 await oldInitial;
 await third.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'))});
 await third.waitForTimeout(30);
 await third.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'))});
 await freshInitial;releaseInitial();
 await expect(third.frameLocator('.a-mcp-app-viewer iframe').locator('body')).toHaveAttribute('data-booted','true');
 await expect(third.locator('.a-mcp-status')).toHaveAttribute('data-phase','ready');
 assert.equal(await mutations(),4);
 await thirdContext.close();
 }
 await mobile.screenshot({path:process.env.MCP_RECOVERY_SCREENSHOT||join(directory,'mobile-recovery.png')});
 assert.deepEqual(errors,[]);
 console.log('MCP clients passed: independent phone/desktop status, foreground refresh, stale response rejection, shared reconnect, iframe/input preservation, no replayed calls; four intended mutations, including a completed call overtaken by disconnect.');
}finally{await browser?.close();await stop();await rm(directory,{recursive:true,force:true})}
