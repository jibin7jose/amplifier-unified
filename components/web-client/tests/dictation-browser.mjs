import {showFixtureConversation} from './browser-chat-pages.mjs';
// Production UI and draft persistence, simulated browser speech events only.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.addInitScript(()=>{window.speechCalls=0;window.SpeechRecognition=class{constructor(){window.speech=this}start(){window.speechCalls++}stop(){this.onend?.()}abort(){this.aborted=true}}});
 const page=await context.newPage(),errors=[],actions=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().endsWith('/api/actions'))actions.push(r.postDataJSON()?.action)});
 await page.goto(url);await showFixtureConversation(page);const input=page.getByRole('textbox',{name:'Message Amplifier'});await input.waitFor();
 const act=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const text=()=>page.evaluate(()=>window.amplifier.getState().view.draft);
 const speak=value=>page.evaluate(value=>speech.onresult({resultIndex:0,results:[Object.assign([{transcript:value}],{isFinal:true})]}),value);
 // Exercise navigation between existing chats. Creating the first chat from
 // the new-chat screen intentionally adopts its saved draft; that is not a
 // cross-chat dictation leak, and depended on the autosave timing here.
 await act('session.create',{title:'Dictation source'});
 const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.ok(first);
 await expect(page.getByRole('button',{name:'Dictate message',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>speechCalls),0);
 await input.fill('Existing draft');await page.getByRole('button',{name:'Dictate message',exact:true}).click();
 await expect(page.getByRole('button',{name:'Stop dictation',exact:true})).toBeVisible();await speak('spoken words');
 await expect.poll(text).toBe('Existing draft spoken words');
 assert.equal(actions.includes('conversation.send'),false);assert.equal(actions.includes('call.start'),false);
 await page.getByRole('button',{name:'Stop dictation',exact:true}).click();
 await page.getByRole('button',{name:'Dictate message',exact:true}).click();await page.evaluate(()=>window.lateSpeech=speech.onresult);
 await act('session.create',{title:'Other chat'});
 const second=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 assert.notEqual(second,first);
 await page.evaluate(()=>lateSpeech({resultIndex:0,results:[Object.assign([{transcript:'Wrong chat'}],{isFinal:true})]}));
 await expect.poll(text).toBe('');assert.equal(await page.evaluate(()=>speech.aborted),true);
 await act('session.select',{id:first});
 await expect(input).toHaveDraft('Existing draft spoken words');
 await act('session.select',{id:second});
 await expect(input).toHaveDraft('');
 await page.getByRole('button',{name:'Dictate message',exact:true}).click();await page.evaluate(()=>speech.onerror({error:'not-allowed'}));
 await expect(page.getByText('Allow microphone access in your browser to dictate.',{exact:true})).toBeVisible();await expect.poll(text).toBe('');
 await input.fill('Send this draft');await page.getByRole('button',{name:'Dictate message',exact:true}).click();
 await page.screenshot({path:'/tmp/unified-dictation97.png'});
 await page.evaluate(()=>window.afterSend=speech.onresult);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(()=>actions.includes('conversation.send')).toBe(true);
 assert.equal(await page.evaluate(()=>speech.aborted),true);
 await page.evaluate(()=>afterSend({resultIndex:0,results:[Object.assign([{transcript:'Late speech'}],{isFinal:true})]}));
 await expect.poll(text).toBe('');
 // Preference applies to this browser and reacts immediately. Keyboard mobile defaults off.
 await page.evaluate(()=>{Object.defineProperty(navigator,'userAgent',{configurable:true,value:'Mozilla Android'});window.dispatchEvent(new Event('dictation-preference'))});
 await act('view.update',{patch:{draft:'Mobile draft'}});await expect(page.getByRole('button',{name:'Dictate message',exact:true})).toHaveCount(0);
 await act('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['voice']}});
 await page.getByLabel('Microphone in the message box').selectOption('show');
 await act('view.update',{patch:{panel:null}});
 await expect(page.getByRole('button',{name:'Dictate message',exact:true})).toBeVisible();
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.setViewportSize({width:1280,height:900});
 await act('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['voice']}});
 await page.getByLabel('Microphone in the message box').selectOption('hide');
 await act('view.update',{patch:{panel:null}});
 await page.reload();await input.waitFor();await expect(page.getByRole('button',{name:'Dictate message',exact:true})).toHaveCount(0);
 assert.deepEqual(errors,[]);
 console.log('Dictation passed: no automatic capture, draft insertion without send/call, navigation cancels stale results, permission error keeps draft, mobile default/override, narrow layout and preference reload. Simulated recognition; no microphone acceptance.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
