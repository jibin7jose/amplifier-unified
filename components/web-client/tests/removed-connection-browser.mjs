import {showFixtureConversation} from './browser-chat-pages.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
import './composer-test-helpers.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/removed_connection_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const actions=[];page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 const inspect=async()=>await(await page.request.get(url+'/fixture')).json();
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'}),retainedDraft='Keep this private draft while replacing the removed connection.';
 await composer.fill(retainedDraft);await expect(composer).toHaveDraft(retainedDraft);
 const before=await inspect();
 await openSettingsPage(page,'ai-connections');
 const connections=page.locator('[data-part="ai-connections"]');
 await connections.getByRole('button',{name:/removed-account/}).click();
 await connections.getByRole('button',{name:'Remove connection',exact:true}).click();
 await expect(connections.getByText('Connection removed. Chats that used it will need a replacement connection when resumed.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveDraft(retainedDraft);
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 const panel=page.getByRole('dialog',{name:'Chat options',exact:true});
 await showComposerOption(page,'provider');
 await expect(panel.locator('#chat-provider')).toBeEnabled();
 try{await expect(panel.locator('#chat-provider option[value="replacement-account"]')).toHaveCount(1)}catch(error){
  console.error(JSON.stringify({actions,inspect:await inspect()}));
  console.error(JSON.stringify(await page.evaluate(()=>{const s=window.amplifier.getState();return {selectedSessionId:s.selectedSessionId,runtimeControl:s.runtimeControl,configurationRevision:s.configurationRevision,actionStatus:s.actionStatus,error:s.error,configurationRefresh:s.sessions.find(row=>row.id===s.selectedSessionId)?.configurationRefresh,panel:document.querySelector('.a-model-popover')?.innerText}})));throw error;
 }
 await expect(panel.locator('#chat-provider')).toHaveValue('removed-account');
 await expect(panel.locator('#chat-provider option[value="removed-account"]')).toBeDisabled();
 assert.deepEqual((await inspect()).started,[],'Browsing a failed chat cannot start its worker');
 await panel.locator('#chat-provider').selectOption('replacement-account');
 await expect.poll(async()=>(await inspect()).pending).toEqual({instance:'replacement-account',model:'new-model'});
 await panel.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.reload();
 await expect(page.getByRole('button',{name:'Model and reasoning settings',exact:true})).toContainText('new-model');
 await expect(composer).toHaveDraft(retainedDraft);
 const queued=await inspect();assert.deepEqual(queued.started,[]);assert.deepEqual(queued.sent,[]);
 assert.deepEqual(queued.messages,before.messages,'Selection must preserve the uncertain prior input');
 assert.equal(queued.savedSelection.instance,'removed-account','Only explicit Send may replace the saved runtime pin');
 await composer.fill('Only this new request is authorized');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Replacement received the new message.',{exact:true})).toBeVisible();
 const after=await inspect();assert.equal(after.started.length,1);assert.equal(after.sent.length,1);
 assert.equal(after.sent[0].text,'Only this new request is authorized');assert.equal(after.sent[0].selection.instance,'replacement-account');
 assert.notEqual(after.sent[0].inputId,'uncertain-prior-input');
 assert.deepEqual(after.messages.find(row=>row.inputId==='uncertain-prior-input'),before.messages[0]);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['real Settings connection removal','real scoped settings and bundle catalog composition','missing saved provider retained until explicit replacement','nonempty private draft and exact history survive removal, replacement and reload together','replacement selection survives reload without starting worker','exact one new send without prior replay'],limits:['synthetic provider metadata and execution; native worker pin restoration is covered separately']}));
}finally{try{await browser?.close()}finally{fixture.kill('SIGTERM')}}
