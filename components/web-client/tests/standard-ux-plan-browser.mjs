import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
// Canonical plan data through public placement and Canvas controls.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row)}}catch{}})});
 const url=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const r=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()};
 const {alpha}=await control({op:'heavy',messages:105,otherMessages:1,chars:50,nodes:0});
 await control({op:'chat-plan',status:'idle'});
 const before=await control({op:'inspect'}),original=before.state.sessions.find(row=>row.id===alpha);
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:headers}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep the private plan draft');
 const plan=page.getByRole('region',{name:'Plan',exact:true});
 const check=async placement=>{await expect(plan).toHaveCount(1);await expect(plan).toHaveClass(new RegExp(`a-chat-plan-${placement}`));await expect(plan).toContainText('1 of 3 done');if(!await plan.getByText('Prepare a document to share',{exact:true}).isVisible())await plan.locator('summary').click();await expect(plan.getByText('Prepare a document to share',{exact:true})).toBeVisible();await expect(plan.locator('input,textarea,select,button,[contenteditable=true]')).toHaveCount(0);assert.deepEqual((await control({op:'inspect'})).state.sessions.find(row=>row.id===alpha).execution.plan,original.execution.plan)};
 await check('composer');
 const placement=async value=>{await openSettingsPage(page,'appearance');await page.getByLabel('Plan location',{exact:true}).selectOption(value);await expect(page.getByLabel('Plan location',{exact:true})).toBeEnabled();await page.getByRole('button',{name:'Close panel',exact:true}).click()};
 await placement('inline');await check('inline');
 await placement('overview');await expect(plan).toHaveCount(0);await page.getByRole('button',{name:'Open canvas',exact:true}).click();const canvas=page.locator('#workspace-canvas');await canvas.getByRole('tab',{name:'Chat overview',exact:true}).click();await check('overview');
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();await placement('composer');await check('composer');
 await control({op:'patch',sessions:{[alpha]:{execution:{...original.execution,plan:{...original.execution.plan,items:[],unavailable:true}}}}});
 await expect(plan).toContainText('Details unavailable');await expect(plan).toContainText('The latest plan could not be read.');await expect(plan).not.toContainText('1 of 3 done');await expect(plan).not.toContainText('completed');await expect(plan.locator('input,textarea,select,button,[contenteditable=true]')).toHaveCount(0);
 const after=await control({op:'inspect'});assert.deepEqual(after.state.sessions.find(row=>row.id===alpha).messages,original.messages);assert.equal(after.runtimeStarts,before.runtimeStarts);assert.equal(after.runtimeSends,0);assert.deepEqual(after.runtimeControls,[]);await expect(composer).toHaveDraft('Keep the private plan draft');assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,publicPlacements:['composer','inline','overview'],readOnly:true,sameCanonicalPlan:true,unavailableTruthful:true,savedMessagesAndDraftRetained:true,runtimeEffects:0}));
}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch{}finally{clearTimeout(timer)}}}}
