import {showFixtureConversation} from './browser-chat-pages.mjs';
import {clearComposer} from './composer-test-helpers.mjs';
// Packaged application and real action dispatch, synthetic media/runtime/discovery.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/voice_visual_ui_server.py','--onboarding-complete'],{stdio:['ignore','pipe','inherit']});
let browser,page,failure,discovery,fixtureUrl;
const calls=[],transitions=[];
const output=process.env.AMPLIFIER_SCREENSHOT_DIR||'/tmp/amplifier-composer-primary-action';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 fixtureUrl=url;
 await mkdir(output,{recursive:true});browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 await page.addInitScript(()=>{
  class Peer extends EventTarget{constructor(){super();this.connectionState='new'}createDataChannel(){const channel=new EventTarget();channel.readyState='open';channel.close=()=>{};return channel}addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(value){this.localDescription=value}async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){}}
  window.RTCPeerConnection=Peer;window.delayMicrophone=true;
  navigator.mediaDevices.getUserMedia=async()=>{if(window.denyMicrophone)throw new DOMException('Synthetic denial','NotAllowedError');if(window.delayMicrophone)await new Promise(resolve=>window.releaseMicrophone=resolve);return new MediaStream()};
 });
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'}),primary=page.locator('[data-part="composer-primary-action"]');await composer.waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const matches=async(label,action,icon)=>{
  await expect(primary).toHaveCount(1);await expect(primary).toHaveAccessibleName(label);await expect(primary).toHaveAttribute('data-action',action);await expect(primary.locator('svg')).toHaveClass(new RegExp('lucide-'+icon));
  transitions.push(await page.evaluate(label=>{const state=window.amplifier.getState();return {label,sessionId:state.selectedSessionId,panel:state.view.panel,voiceConfiguration:state.voiceConfiguration,voiceStatus:state.voice.status,settingsPage:document.querySelector('[data-part="settings-experience"]')?.getAttribute('data-settings-page'),focus:document.activeElement?.getAttribute('aria-label')}},label));
  // Shared tooltips remove native title on hover/focus; accessible action names
  // stay authoritative across voice transitions (tooltip behavior has its own test).
 };
 await matches('Start voice call','call.start','audio-lines');await expect(primary).toBeEnabled();await expect(page.locator('.a-call-button')).toHaveCount(0);
 // Exercise the real default/catalog UI instead of avoiding discovery to get
 // clean shutdown. The synthetic metadata matches the fixture's seeded connection.
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 const options=page.getByRole('dialog',{name:'Model for this chat',exact:true});
 await expect(options.locator('#chat-provider')).toHaveValue('fixture');
 await expect(options.locator('#chat-provider')).toBeEnabled();
 await expect(options.locator('#chat-model')).toHaveValue('fixture-model');
 await expect(options.locator('#chat-model')).toBeEnabled();
 await expect(options.locator('#chat-model option[value="fixture-model"]')).toBeAttached();
 await options.getByRole('button',{name:'Close chat options',exact:true}).click();
 await expect(options).toHaveCount(0);
 await matches('Start voice call','call.start','audio-lines');
 await composer.press('Enter');assert.equal(calls.filter(row=>row.action==='call.start').length,0,'Empty Enter never starts voice');
 await composer.fill(' \n');await matches('Start voice call','call.start','audio-lines');await clearComposer(composer);
 await primary.click();await matches('End voice call','call.end','x');await expect.poll(()=>page.evaluate(()=>typeof window.releaseMicrophone)).toBe('function');
 await composer.press('Enter');assert.equal(calls.filter(row=>row.action==='call.end').length,0,'Empty Enter never ends voice');
 await primary.click();await matches('Start voice call','call.start','audio-lines');
 await page.evaluate(()=>{window.delayMicrophone=false;window.releaseMicrophone()});
 await primary.click();await matches('End voice call','call.end','x');
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('button',{name:'Advanced',exact:true}).click();
 await page.getByRole('button',{name:/^Computer access/}).click();
 await expect(page.getByRole('button',{name:'Choose screen source',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const activity=async(status,workers=[])=>{assert.equal((await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status,workers}})).status(),200)};
 const ends=calls.filter(row=>row.action==='call.end').length;
 await composer.fill('A typed message during voice');await matches('Send message','conversation.send','arrow-up');await expect(primary).toHaveAttribute('type','submit');
 await primary.click();await expect.poll(async()=>(await inspect()).sent.length).toBe(1);await matches('End voice call','call.end','x');
 assert.equal((await inspect()).sent[0].text,'A typed message during voice');assert.equal(calls.filter(row=>row.action==='call.end').length,ends,'Sending text keeps the call connected');
 await action('view.update',{patch:{draft:'An agent supplied draft'}});await expect(composer).toHaveDraft('An agent supplied draft');await matches('Send message','conversation.send','arrow-up');await action('view.update',{patch:{draft:''}});await matches('End voice call','call.end','x');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'note.txt',mimeType:'text/plain',buffer:Buffer.from('Attachment-only message')});await page.getByRole('button',{name:'Remove note.txt'}).waitFor();await matches('Send message','conversation.send','arrow-up');await expect(primary).toBeEnabled();
 await page.getByRole('button',{name:'Remove note.txt'}).click();await matches('End voice call','call.end','x');
 await activity('working');await matches('End voice call','call.end','x');await primary.click();await matches('Stop response','conversation.stop','x');assert.deepEqual((await inspect()).stopped,[],'Ending voice does not cancel background work');
 await composer.fill('A correction');await matches('Send message','conversation.send','arrow-up');
 const draftStop=page.getByRole('button',{name:'Stop response',exact:true});await expect(draftStop).toBeVisible();await expect(draftStop).toBeEnabled();
 await draftStop.click();await expect(composer).toHaveDraft('A correction');await expect.poll(async()=>(await inspect()).stopped).toEqual([sid]);
 await activity('working');await clearComposer(composer);await matches('Stop response','conversation.stop','x');
 await primary.focus();await page.keyboard.press('Space');await expect.poll(async()=>(await inspect()).stopped).toEqual([sid,sid]);await matches('Start voice call','call.start','audio-lines');
 await activity('idle',[{id:'worker',status:'running',title:'Background worker'}]);await matches('Stop response','conversation.stop','x');await activity('stopping');await expect(primary).toBeDisabled();await activity('idle');await matches('Start voice call','call.start','audio-lines');
 await page.evaluate(()=>window.denyMicrophone=true);await primary.click();await expect(page.getByText(/Microphone access was declined/)).toBeVisible();await matches('Start voice call','call.start','audio-lines');await expect(primary).toBeEnabled();await page.evaluate(()=>window.denyMicrophone=false);
 await page.getByRole('button',{name:'Dismiss error',exact:true}).click();
 await action('view.update',{patch:{navPinned:false,navExpanded:false}});
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});await clearComposer(composer);await activity('idle');await matches('Start voice call','call.start','audio-lines');const box=await primary.boundingBox();await page.screenshot({path:output+`/voice-${width}.png`});
  await composer.fill('Ready to send');await matches('Send message','conversation.send','arrow-up');assert.deepEqual(await primary.boundingBox(),box);await page.screenshot({path:output+`/send-${width}.png`});
  await clearComposer(composer);await activity('working');await matches('Stop response','conversation.stop','x');assert.deepEqual(await primary.boundingBox(),box);await expect(primary).toBeInViewport();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:output+`/cancel-${width}.png`});
 }
 assert.deepEqual(errors,[]);assert.equal(calls.filter(row=>row.action==='conversation.send').length,1);
 discovery=(await inspect()).discovery;
 assert.equal(discovery.mode,'synthetic-metadata');
 assert.ok(discovery.calls['configuration.defaults']+discovery.calls['configuration.catalog']>0,'Actual default/catalog requests must reach the synthetic resolution seam');
 assert.equal(discovery.refused['probe.spawn'],0);
 assert.deepEqual(Object.values(discovery.refused),Object.values(discovery.refused).map(()=>0));
 await writeFile(output+'/primary-action.json',JSON.stringify({passed:true,calls,transitions,discovery},null,2));
 console.log('Composer primary action passed: voice/send/cancel, connecting cancellation, text/attachments during voice, agent drafts, work versus call cancellation, keyboard, permission recovery, stable desktop/mobile geometry. Synthetic media/runtime/provider metadata; no physical audio or real acquisition claim.');
}catch(error){
 failure=error;
 if(fixtureUrl&&page&&!page.isClosed())discovery=await page.request.get(fixtureUrl+'/fixture').then(response=>response.json()).then(value=>value.discovery).catch(()=>discovery);
 const state=page&&!page.isClosed()?await page.evaluate(()=>{const state=window.amplifier?.getState();return {sessionId:state?.selectedSessionId,view:state?.view,voice:state?.voice,voiceConfiguration:state?.voiceConfiguration}}).catch(()=>null):null;
 await writeFile(output+'/primary-action.json',JSON.stringify({passed:false,error:String(error),calls,transitions,state,discovery},null,2));
 if(page&&!page.isClosed())await page.screenshot({path:output+'/failure.png'}).catch(()=>{});
 throw error;
}
finally{
 const cleanupErrors=[];
 try{await browser?.close()}catch(error){cleanupErrors.push(error)}
 try{
  if(fixture.exitCode===null&&fixture.signalCode===null){
   const exited=once(fixture,'exit');let timer;
   fixture.kill('SIGTERM');
   try{
    await Promise.race([exited,new Promise((_,reject)=>{
     timer=setTimeout(()=>{
      fixture.kill('SIGKILL');
      reject(Error('Composer fixture did not finish graceful shutdown within 5 seconds'));
     },5000);
    })]);
   }finally{clearTimeout(timer)}
  }
  assert.equal(fixture.exitCode,0,'Composer fixture must finish its cleanup');
  assert.equal(fixture.signalCode,null,'Composer fixture must exit normally');
 }catch(error){cleanupErrors.push(error)}
 if(cleanupErrors.length)throw new AggregateError([...(failure?[failure]:[]),...cleanupErrors],'Composer cleanup failed');
}
