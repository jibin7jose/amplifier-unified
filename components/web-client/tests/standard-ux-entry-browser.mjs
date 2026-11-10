import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public setup/recovery entry with private synthetic configuration prerequisites.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();
 // Establish a returning first-use setup state; all route transitions below
 // use the same controls available to an ordinary user.
 await act('view.update',{patch:{setupJourney:'welcome'}});
 const welcome=page.getByRole('region',{name:'Get started',exact:true});await expect(welcome).toContainText('Your first connection is ready');
 await expect(welcome.getByRole('button',{name:'Set up with the assistant',exact:true})).toBeEnabled();
 await welcome.getByRole('button',{name:'Start a chat',exact:true}).click();await expect(welcome).toHaveCount(0);await expect(composer).toBeEditable();
 assert.equal((await inspect()).sent.length,0);assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.length),0);
 const owner=await act('session.create',{title:'Recovery owner'});await composer.fill('Private recovery draft');await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('Private recovery draft');
 const before=await inspect(),clientId=await page.evaluate(()=>window.amplifier.shellClientId);
 const original=(await act('shell.inspect',{clientId})).result;
 const composition=structuredClone(original.composition);composition.instances=composition.instances.filter(row=>row.package!=='builtin.chats');
 const prepared=await act('shell.changes.prepare',{clientId,expectedRevision:original.revision,composition});await act('shell.changes.apply',{clientId,expectedRevision:original.revision,changeId:prepared.result.id});
 const recovery=page.getByRole('link',{name:'Open with standard navigation',exact:true});await expect(recovery).toBeVisible();
 const stored=(await act('shell.inspect',{clientId})).result;
 await Promise.all([page.waitForEvent('load'),recovery.click()]);assert.equal(new URL(page.url()).searchParams.get('shell'),'recovery');await expect(composer).toHaveDraft('Private recovery draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),owner.sessionId);
 if(await page.getByRole('button',{name:'Open navigation',exact:true}).isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.getByRole('button',{name:'Search chats',exact:true}).click();await expect(page.getByRole('heading',{name:'Search chats',exact:true})).toBeVisible();
 const reloaded=(await act('shell.inspect',{clientId:await page.evaluate(()=>window.amplifier.shellClientId)})).result;
 assert.deepEqual(reloaded.composition,stored.composition,'Recovery must not overwrite saved shell configuration');
 const after=await inspect();assert.deepEqual(after.sent,before.sent);assert.deepEqual(after.messages,before.messages);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,checks:['setup guidance','public Start a chat','no implicit model work','public standard-navigation recovery','retained selection and private draft','saved configuration unchanged','search functional after recovery'],limits:['synthetic available provider; no real account authorization']}));
}finally{await browser?.close();fixture.kill()}
