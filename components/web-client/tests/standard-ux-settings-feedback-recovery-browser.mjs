import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real notices and local attachment staging. No report is submitted or uploaded.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/ordinary_recovery_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1366,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),actions=[],errors=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});
 const facts=async()=>{const response=await page.request.get(boot.url+'/fixture/ordinary-recovery');assert.ok(response.ok());return response.json()};
 await page.goto(boot.url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Retain this unsent ordinary recovery draft');
 const before=await facts(),notice=before.attention.items.find(row=>row.id==='notifications:error'),other=before.attention.items.find(row=>row.page==='updates'&&!row.read);assert.ok(notice&&!notice.read);assert.ok(other);
 await openSettingsPage(page,'notifications');await page.bringToFront();await expect(page.getByText(/Synthetic delivery notice one/)).toBeVisible();
 await expect.poll(async()=>(await facts()).attentionRead[notice.id]).toBe(notice.fingerprint);
 const after=await facts();assert.ok(!after.attention.items.find(row=>row.id===other.id).read,'Unvisited update notice stays unread');assert.equal(after.sessionsUnchanged,true);
 const reads=actions.filter(row=>row.action==='attention.read');assert.deepEqual(reads.map(row=>row.args.ids),[[notice.id]]);assert.deepEqual(reads[0].args.fingerprints,{[notice.id]:notice.fingerprint});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(composer).toHaveDraft('Retain this unsent ordinary recovery draft');
 await page.getByRole('button',{name:/^App options/}).click();await page.getByRole('button',{name:'Send feedback',exact:true}).click();
 await page.getByLabel('Title',{exact:true}).fill('Local attachment recovery');await page.getByLabel('Details',{exact:true}).fill('Synthetic local proof; do not submit.');
 const writes=[];let drop=true;
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='feedback.attachment.add'){writes.push(body);if(drop){drop=false;const response=await route.fetch();assert.ok(response.ok());await route.abort('connectionreset');return}}await route.continue()});
 const content='Exactly one local attachment after a lost acknowledgement';
 await page.getByLabel('Choose feedback files').setInputFiles({name:'recovery.txt',mimeType:'text/plain',buffer:Buffer.from(content)});
 const check=page.getByRole('button',{name:'Check attachment: recovery.txt',exact:true});await expect(check).toBeEnabled();assert.equal((await facts()).attachments.length,1);
 await check.click();await expect(check).toHaveCount(0);await expect(page.getByRole('button',{name:'Preview recovery.txt',exact:true})).toHaveCount(1);
 assert.equal(writes.length,2);assert.deepEqual(writes[0].args,writes[1].args);const saved=await facts();assert.equal(saved.attachments.length,1);assert.equal(saved.attachments[0].requestId,writes[0].args.requestId);assert.equal(saved.attachments[0].bytes,content);assert.equal(saved.sessionsUnchanged,true);
 assert.equal(actions.filter(row=>['feedback.submit','feedback.destination','conversation.send','session.warm'].includes(row.action)).length,0);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,exactVisibleNoticeOnly:true,unvisitedNoticeUnread:true,historyAndDraftRetained:true,lostAttachmentAckSameIdentity:true,localFiles:1,outboundSubmissions:0}));
}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch{}finally{clearTimeout(timer)}}}}
