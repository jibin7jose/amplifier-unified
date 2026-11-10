import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {build} from 'esbuild';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_OUTPUT||join(tmpdir(),'smart-canvas-owner-evidence');
await mkdir(out,{recursive:true});
const documents=await mkdtemp(join(tmpdir(),'smart-canvas-owner-docs-'));
for(const label of ['alpha','bravo']){
 const source=`import {App} from '@modelcontextprotocol/ext-apps';
const app=new App({name:${JSON.stringify(label)},version:'1.0.0'},{});
document.querySelector('button').onclick=async()=>{try{const result=await app.readServerResource({uri:'counter://media/${label}'});document.querySelector('output').textContent=result.contents[0].text}catch(error){document.querySelector('output').textContent=error.message}};
await app.connect();document.body.dataset.ready='true';`;
 const built=await build({stdin:{contents:source,resolveDir:process.cwd(),sourcefile:label+'.js'},bundle:true,write:false,format:'esm',minify:true});
 await writeFile(join(documents,label+'.html'),`<!doctype html><html><body><h1>${label} view</h1><button>Read ${label} resource</button><output></output><input aria-label="Local tool draft"><script type="module">${built.outputFiles[0].text.replaceAll('</script','<\\/script')}</script></body></html>`);
}
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/smart_canvas_owner_ui_server.py',documents],{stdio:['ignore','pipe','pipe']});
let browser,page,failure,logs='';const checks=[],errors=[];
fixture.stderr.on('data',chunk=>logs+=chunk);
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),25000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;logs+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 const observed=()=>page.request.get(ready.url+'/fixture/observations').then(response=>response.json());
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const record=async()=>writeFile(out+'/observations.json',JSON.stringify({checks,observations:await observed(),state:await state(),errors},null,2)+'\n');
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();await composer.fill('Keep this private chat draft');
 const original=(await observed()).messages;
 const tools=page.locator('[data-part="smart-tools-settings"][data-simple="true"]');
 const choose=async label=>{
  await openSettingsPage(page,'smart-tools');
  // This is the first page of its ordinary Settings section; no helper fallback.
  await tools.getByRole('button',{name:'Installed',exact:true}).click();
  await tools.getByRole('button',{name:new RegExp('^'+label+' counter ')}).click();
  await tools.getByRole('button',{name:/^Counter read /}).click();
  await expect(tools.getByRole('button',{name:'Open interactive view',exact:true})).toBeEnabled();
  await tools.getByRole('button',{name:'Open interactive view',exact:true}).click();
 };
 await choose('Alpha');
 await expect.poll(async()=>(await observed()).held.entered).toBe(true);
 await expect(tools).toContainText('Opening the canvas view…');
 await choose('Bravo');
 const frame=page.frameLocator('.a-canvas-html');
 await expect(frame.getByRole('heading',{name:'bravo view',exact:true})).toBeVisible();
 await expect(frame.locator('body')).toHaveAttribute('data-ready','true');
 const bravo=(await state()).canvas.id;
 await frame.getByRole('textbox',{name:'Local tool draft',exact:true}).fill('Keep bravo local draft');
 await page.request.post(ready.url+'/fixture/release');
 await expect.poll(async()=>(await observed()).operations.find(row=>row.action==='smartTools.open'&&row.target.id==='alpha')?.status).toBe('completed');
 await record();
 await expect.poll(async()=>(await state()).canvas.id).toBe(bravo);
 await expect(frame.getByRole('heading',{name:'bravo view',exact:true})).toBeVisible();
 await expect(frame.getByRole('textbox',{name:'Local tool draft',exact:true})).toHaveValue('Keep bravo local draft');
 checks.push('Late Alpha document completion preserves explicitly newer Bravo view and its mounted local draft.');
 // Execute the old resource read, but hold delivery until a different view owns Canvas.
 let heldResponse,markHeld,releaseResource,markDelivered;
 const entered=new Promise(resolve=>markHeld=resolve),gate=new Promise(resolve=>releaseResource=resolve),delivered=new Promise(resolve=>markDelivered=resolve);
 const resource=url=>url.pathname.includes('/api/canvas/')&&url.pathname.endsWith('/resources')&&url.searchParams.get('kind')==='read';
 const holdResource=async route=>{heldResponse=await route.fetch();markHeld();await gate;try{await route.fulfill({response:heldResponse})}catch(error){if(!/Target.*closed|aborted|Invalid InterceptionId/i.test(error.message))throw error}finally{markDelivered()}};
 await page.route(resource,holdResource);
 await frame.getByRole('button',{name:'Read bravo resource',exact:true}).click();await entered;
 await choose('Alpha');await expect(frame.getByRole('heading',{name:'alpha view',exact:true})).toBeVisible();
 const alpha=(await state()).canvas.id;
 await frame.getByRole('textbox',{name:'Local tool draft',exact:true}).fill('Keep alpha local draft');
 releaseResource();await delivered;await page.unroute(resource,holdResource);
 await expect(frame.getByRole('heading',{name:'alpha view',exact:true})).toBeVisible();
 await expect(frame.locator('output')).toHaveText('');
 assert.equal((await state()).canvas.id,alpha);
 await expect(frame.getByRole('textbox',{name:'Local tool draft',exact:true})).toHaveValue('Keep alpha local draft');
 await frame.getByRole('button',{name:'Read alpha resource',exact:true}).click();await expect(frame.locator('output')).toHaveText('Retained alpha');
 checks.push('Already-executed old resource response cannot replace the current iframe, result, selection, or local draft; current owner reads its own resource.');
 await record();
 // A close/reopen returns the same artifact ID but is a newer presentation intent.
 await page.request.post(ready.url+'/fixture/hold',{data:{identity:'bravo'}});
 await choose('Bravo');await expect.poll(async()=>(await observed()).held.entered).toBe(true);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.getByRole('button',{name:'Close canvas panel',exact:true}).click();
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 await expect(frame.getByRole('heading',{name:'alpha view',exact:true})).toBeVisible();
 assert.equal((await state()).canvas.id,alpha);
 await expect(frame.getByRole('textbox',{name:'Local tool draft',exact:true})).toHaveValue('Keep alpha local draft');
 await openSettingsPage(page,'smart-tools');
 await tools.getByRole('button',{name:/^Bravo counter /}).click();
 await tools.getByRole('button',{name:/^Counter read /}).click();
 await expect(tools.getByRole('heading',{name:'Counter read',exact:true})).toBeVisible();
 await page.request.post(ready.url+'/fixture/release');
 await expect.poll(async()=>(await observed()).operations.filter(row=>row.action==='smartTools.open'&&row.target.id==='bravo').at(-1)?.status).toBe('completed');
 await expect(tools.getByRole('button',{name:'Open interactive view',exact:true})).toBeEnabled();
 await expect(tools).toContainText('Saved in Canvas. Your current view is unchanged.');
 assert.equal((await state()).canvas.id,alpha);
 await expect(page.locator('.a-settings-experience')).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(frame.getByRole('textbox',{name:'Local tool draft',exact:true})).toHaveValue('Keep alpha local draft');
 checks.push('Close/reopen ABA retains the same current artifact and local draft; late saved-only completion does not dismiss the newer Settings visit.');
 const final=await observed();assert.deepEqual(final.started,[]);assert.deepEqual(final.sent,[]);assert.deepEqual(final.messages,original);assert.equal((await state()).selectedSessionId,ready.sessionId);assert.equal((await state()).view.draft,'Keep this private chat draft');
 assert.equal(final.operations.filter(row=>row.action==='smartTools.open').length,4);assert.deepEqual(errors,[]);
 checks.push('Exactly four explicit view opens, no message/runtime calls, saved chat bytes and private composer draft retained.');
 await record();await page.screenshot({path:out+'/passed.png'});console.log(JSON.stringify({passed:true,checks}));
}catch(error){failure=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}
 if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(error){cleanup.push(error)}finally{clearTimeout(timer)}}
 await writeFile(out+'/fixture.log',logs);await rm(documents,{recursive:true,force:true});
 if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Smart Canvas cleanup failed');
}
