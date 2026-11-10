import {showFixtureConversation} from './browser-chat-pages.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
// Public exact-instance checks plus ordinary composer capability/pending states.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
const unwrap=raw=>raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;if(!out)throw Error('Owned artifacts required');await mkdir(out,{recursive:true});const observations=[];
async function run(mode,check){
 const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/standard_connection_inspection_ui_server.py',...(mode==='composer'?['--composer']:[])],{stdio:['ignore','pipe','pipe']});let browser,page,logs='',release,failure;fixture.stderr.on('data',d=>logs+=d);const errors=[];
 try{const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',d=>{text+=d;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
  browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',e=>errors.push(e.message));const facts=async()=>{const response=await page.request.get(ready.url+'/fixture/inspection');assert.ok(response.ok());return response.json()};
  await check({page,url:ready.url,facts,setRelease:value=>{release=value}});assert.deepEqual(errors,[]);await writeFile(out+'/observations.json',JSON.stringify({passed:false,completedModes:observations},null,2)+'\n');await page.screenshot({path:out+'/'+mode+'.png'});
 }catch(e){failure=e;await page?.screenshot({path:out+'/'+mode+'-failure.png'}).catch(()=>{});throw e}
 finally{release?.();const cleanup=[];try{await browser?.close()}catch(e){cleanup.push(e)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(e){cleanup.push(e)}finally{clearTimeout(timer)}}await writeFile(out+'/'+mode+'-fixture.log',logs)}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Inspection cleanup failed')}
}
await run('connections',async({page,url,facts})=>{
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep private inspection draft');const before=await facts();await openSettingsPage(page,'ai-connections');const panel=page.locator('[data-part="ai-connections"]'),requests=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions'){const raw=r.postDataJSON(),body=raw.action==='shell.command'?(raw.args.command||raw.args):raw;if(body.action==='providers.test')requests.push(body)}});
 for(const id of ['one','two']){await panel.getByRole('button',{name:new RegExp('OpenAI API.*'+id)}).click();await panel.getByText('Account and connection checks',{exact:true}).click();await panel.getByRole('button',{name:'Check connection',exact:true}).click();await expect(panel.getByText(id==='one'?'Model catalog check passed.':'Connection check failed.',{exact:true})).toBeVisible();const observed=await facts();assert.equal(observed.test.providerId,id);assert.equal(observed.test.reachable,id==='one');assert.equal(observed.test.modelCount,id==='one'?7:0);assert.equal(requests.at(-1).args.id,id);assert.deepEqual(requests.at(-1).args.configurationTarget,{kind:'global'});await panel.getByRole('button',{name:'Back',exact:true}).click();}
 assert.deepEqual(requests.map(row=>row.args.id),['one','two']);const after=await facts();assert.deepEqual(after.calls.filter(row=>row.action==='providers.test').map(row=>row.id),['one','two']);assert.deepEqual(after.sessionIds,before.sessionIds);assert.deepEqual(after.sent,[]);await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(composer).toHaveDraft('Keep private inspection draft');observations.push({id:'connection-inspection-exact-owner',passed:true,limits:['Synthetic catalog success/refusal; no external account or credential transport.']});
});
await run('composer',async({page,url,facts,setRelease})=>{
 await page.setViewportSize({width:390,height:844});await page.goto(url);await showFixtureConversation(page,{navPinned:false});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep pending model draft');await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();await expect.poll(async()=>(await facts()).calls.filter(row=>row.action==='configuration.defaults').length).toBeGreaterThan(0);const options=page.getByRole('dialog',{name:'Chat options',exact:true});await expect(options).toBeVisible();
 await showComposerOption(page,'provider');await expect(options.locator('#chat-provider')).toHaveValue('');await expect(options.locator('#chat-provider option[value="test-provider"]')).toHaveCount(1);await expect(options.locator('#chat-provider option[value="no-effort"]')).toHaveCount(1);
 await showComposerOption(page,'model');await expect(options.locator('#chat-model')).toBeDisabled();assert.equal(await options.getByText('No providers configured',{exact:true}).count(),0);await expect(composer).toHaveDraft('Keep pending model draft');assert.deepEqual((await facts()).sent,[]);
 await page.request.post(url+'/fixture/release-defaults');await showComposerOption(page,'provider');await expect(options.locator('#chat-provider')).toHaveValue('test-provider');await options.getByRole('button',{name:'Close chat options',exact:true}).click();observations.push({id:'options-pending-truthful',passed:true,observation:'Cached providers remain selectable while unresolved default has no selected provider and model is disabled; exact default arrives only after preparation release.'});
 const modelButton=page.getByRole('button',{name:'Model and reasoning settings',exact:true}),model=page.getByRole('dialog',{name:'Chat options',exact:true}),effort=model.locator('#chat-effort');
 const closeModel=async()=>{
  // Local dismissal is optimistic. Reload only after this exact public view
  // command is acknowledged, so the new page copies the confirmed closed view.
  const saved=page.waitForResponse(response=>{
   if(!response.url().endsWith('/api/actions')||response.request().method()!=='POST')return false;
   const body=unwrap(response.request().postDataJSON());return body?.action==='view.update'&&body.args?.patch?.composerModel?.open===false;
  });
  await model.getByRole('button',{name:'Close chat options',exact:true}).click();
  const response=await saved;assert.ok(response.ok());const receipt=await response.json();assert.equal(receipt.accepted,true,JSON.stringify(receipt));
  await expect(model).toHaveCount(0);await expect(modelButton).toHaveAttribute('aria-expanded','false');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.composerModel.open)).toBe(false);
 };
 await page.setViewportSize({width:1440,height:1000});await modelButton.click();await showComposerOption(page,'effort');await expect(effort).toBeVisible();await effort.press('End');await expect(effort).toHaveAttribute('aria-valuetext','high');await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection.effort)).toBe('high');await closeModel();await page.reload();await expect(composer).toBeEditable();await expect(model).toHaveCount(0);await expect(modelButton).toHaveAttribute('aria-expanded','false');await modelButton.click();await showComposerOption(page,'effort');await expect(effort).toHaveAttribute('aria-valuetext','high');
 await showComposerOption(page,'provider');await model.locator('#chat-provider').selectOption('no-effort');await showComposerOption(page,'effort');await expect(model.getByText('Reasoning effort is not configurable for this model.',{exact:true})).toBeVisible();await expect(effort).toHaveCount(0);await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection)).toEqual({instance:'no-effort',model:'simple'});await closeModel();await expect(composer).toHaveDraft('Keep pending model draft');const after=await facts();assert.deepEqual(after.sent,[]);assert.deepEqual(after.started,[]);assert.deepEqual(after.sessionIds,[]);observations.push({id:'ordinary-effort-accessible-and-unsupported',passed:true,limits:['Ordinary shared model/options dialog; selected effort and unsupported capability are checked directly. Synthetic capability metadata, no provider request.']});
});
await writeFile(out+'/observations.json',JSON.stringify({passed:true,observations},null,2)+'\n');
