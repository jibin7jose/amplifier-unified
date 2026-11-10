// Real rendered controls over HTTP, with conversation startup forbidden.
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const evidenceDir=process.env.AMPLIFIER_DORMANT_EVIDENCE||'/artifacts/dormant-controls';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/dormant_controls_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,peerBrowser,page,log='',primary;
const evidence={kind:'rendered-absent-worker',runtimeAcceptance:false};
fixture.stderr.on('data',data=>{log=(log+data).slice(-20000)});
try{
 const meta=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout: '+log)),60000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+log))});
  fixture.stdout.on('data',data=>{output+=data;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});
 });
 const headers={Authorization:'Bearer dormant-fixture-token','Content-Type':'application/json'};
 const api=async(path,body)=>{const response=await fetch(meta.url+path,{headers,...body===undefined?{}:{method:'POST',body:JSON.stringify(body)}});assert.equal(response.status,200,await response.clone().text());return response.json()};
 const inspect=()=>api('/fixture/inspect');
 const errors=[];
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1360,height:920},extraHTTPHeaders:{Authorization:headers.Authorization}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(meta.url);await showFixtureConversation(page);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(composer).toBeVisible();
 await composer.fill('Keep my unsent draft');
 await expect(composer).toHaveDraft('Keep my unsent draft');
 await expect(page.getByText('Old saved answer',{exact:true})).toBeVisible();
 assert.deepEqual((await inspect()).runtimeCalls,[]);
 await api('/fixture/state',{status:'starting'});
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Chat options',exact:true})).toBeVisible();
 await showComposerOption(page,'bundle');
 const bundles=page.getByRole('combobox',{name:'Registered root bundles',exact:true});
 await expect(bundles).toBeEnabled();
 await bundles.focus();await bundles.press('w');await bundles.press('Enter');
 await expect.poll(async()=>(await inspect()).session.pendingBundleSelection?.bundle).toBe('work');
 await expect(page.getByText(/Next message/).first()).toBeVisible();
 assert.equal((await inspect()).session.bundle,'anchors');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.getByRole('button',{name:'Model and reasoning settings'}).click();
 // A new root with no pin has no proven provider default until discovery.
 // Cached choices remain selectable; explicitly choose their connection.
 await showComposerOption(page,'provider');
 await page.locator('#chat-provider').selectOption('one');
 await expect.poll(async()=>(await inspect()).session.pendingModelSelection?.instance).toBe('one');
 await showComposerOption(page,'model');
 await expect(page.locator('#chat-model')).toBeEnabled();
 await page.locator('#chat-model').selectOption('second');
 await expect.poll(async()=>(await inspect()).session.pendingModelSelection?.model).toBe('second');
 // The combined dialog shares acknowledged revisions across its controls.
 // Keep it open for model -> bundle -> effort; no reload/reopen may repair it.
 await showComposerOption(page,'bundle');
 await bundles.selectOption('anchors');
 await expect.poll(async()=>{const value=(await inspect()).session;return value.pendingBundleSelection?.bundle||value.bundle}).toBe('anchors');
 await showComposerOption(page,'effort');
 await expect(page.locator('#chat-effort')).toBeEnabled();
 await page.locator('#chat-effort').focus();await page.locator('#chat-effort').press('End');
 await expect.poll(async()=>(await inspect()).session.pendingModelSelection).toEqual({instance:'one',model:'second',effort:'high'});
 // A held metadata resolver cannot hold rename, settings saves, or artifacts.
 await showComposerOption(page,'model');
 await page.getByRole('button',{name:'Refresh models',exact:true}).click();
 await expect.poll(async()=>(await inspect()).resolverCalls).toBeGreaterThan(0);
 const act=(name,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[name,args]);
 await act('session.rename',{id:meta.id,title:'Manual retained name'});
 await act('canvas.select',{id:meta.artifactId});
 await expect(page.getByText('Retain this content.',{exact:true}).first()).toBeVisible();
 await act('canvas.close',{});
 await expect(composer).toHaveDraft('Keep my unsent draft');
 const beforeReload=await inspect();
 assert.deepEqual(beforeReload.runtimeCalls,[]);
 assert.equal(beforeReload.session.title,'Manual retained name');
 await page.reload();await expect(composer).toBeVisible();
 await expect(composer).toHaveDraft('Keep my unsent draft');
 assert.deepEqual((await inspect()).session.pendingModelSelection,beforeReload.session.pendingModelSelection);
 // Two connected clients; explicit stale configuration revision must conflict.
 peerBrowser=await chromium.launch({headless:true});
 const peer=await peerBrowser.newPage({extraHTTPHeaders:{Authorization:headers.Authorization}});
 await peer.goto(meta.url);await showFixtureConversation(peer);await expect(peer.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 const oldRevision=(await inspect()).session.nextMessageRevision;
 await act('conversation.configure',{sessionId:meta.id,expectedRevision:oldRevision,selection:{instance:'one',model:'first',effort:'low'}});
 const stale=await peer.evaluate(async args=>{try{await window.amplifier.dispatch('conversation.configure',args);return {accepted:true}}catch(error){return {accepted:false,error:error.message}}},{sessionId:meta.id,expectedRevision:oldRevision,bundle:'anchors'});
 assert.equal(stale.accepted,false);assert.match(stale.error,/changed/i);
 assert.equal((await inspect()).session.pendingModelSelection.model,'first');
 await expect(peer.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('');
 await peerBrowser.close();peerBrowser=null;
 await api('/fixture/state',{resolver:'failed'});
 // Narrow-screen keyboard controls remain usable after resolver failure.
 // Compact Options is the same retained menu across widths, not a new mobile
 // component. Close restored presentation before testing a fresh keyboard open.
 const optionsMenu=page.getByRole('dialog',{name:'Chat options',exact:true});
 if(await optionsMenu.isVisible())await optionsMenu.getByRole('button',{name:'Close chat options',exact:true}).click();
 await expect(optionsMenu).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).focus();
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).press('Enter');
 await expect(page.getByRole('dialog',{name:'Chat options'})).toBeVisible();
 await page.getByRole('dialog',{name:'Chat options',exact:true}).getByRole('button',{name:'Choose agent bundle',exact:true}).press('Enter');
 await expect(page.locator('#composer-options-bundle')).toBeEnabled();
 await page.locator('#composer-options-bundle').focus();await page.locator('#composer-options-bundle').press('w');await page.locator('#composer-options-bundle').press('Enter');
 await expect.poll(async()=>{const value=(await inspect()).session;return value.pendingBundleSelection?.bundle||value.bundle}).toBe('work');
 await expect(composer).toHaveDraft('Keep my unsent draft');
 await mkdir(evidenceDir,{recursive:true});
 await page.screenshot({path:evidenceDir+'/dormant-controls-narrow.png',fullPage:true});
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.setViewportSize({width:1360,height:920});
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await page.screenshot({path:evidenceDir+'/dormant-controls-desktop.png',fullPage:true});
 const final=await inspect();assert.deepEqual(final.runtimeCalls,[]);assert.deepEqual(errors,[]);
 evidence.result={workerStarts:0,prewarmStarts:0,modelCalls:0,inputStarts:0,desktop:true,narrowKeyboard:true,sameDialogModelBundleEffort:true,staleClientRefused:true,manualName:final.session.title,finalRevision:final.session.nextMessageRevision,heldResolverIndependent:true,errors};
 console.log(JSON.stringify(evidence));
}catch(error){primary=error;evidence.failure={message:error.message,fixtureStderr:log};throw error}
finally{
 await mkdir(evidenceDir,{recursive:true});
 await writeFile(evidenceDir+'/browser-result.json',JSON.stringify(evidence,null,2));
 for(const owned of [peerBrowser,browser])await owned?.close();
 if(fixture.exitCode===null){
  const done=once(fixture,'exit');fixture.kill('SIGTERM');let timer;
  try{await Promise.race([done,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture cleanup timeout')),10000)})])}
  catch(error){if(fixture.exitCode===null&&fixture.signalCode===null){console.error('Reaping owned fixture PID',fixture.pid);fixture.kill('SIGKILL');await done}if(!primary)throw error}
  finally{clearTimeout(timer)}
 }
}
