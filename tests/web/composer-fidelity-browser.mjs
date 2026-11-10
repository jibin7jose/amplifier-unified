// Approved Compact A disclosure, against the actual bundled UI and synthetic host.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/composer-fidelity';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,page;
const evidence={geometry:[],limits:['Synthetic host: no provider, physical keyboard, microphone or live user draft','Real quote ownership is qualified by the separately maintained multi-quote journey']};
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timeout=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}})});
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 if(process.env.AMPLIFIER_APPROVED_COMPOSER_HTML){
  const html=await readFile(process.env.AMPLIFIER_APPROVED_COMPOSER_HTML,'utf8');
  evidence.approvedHtmlSha256=createHash('sha256').update(html).digest('hex');
  assert.equal(evidence.approvedHtmlSha256,'4c10fe2520a02b7df1c7804b44984bb18cf80eb606358674cce619e3ff6cb424');
  const reference=await browser.newPage({viewport:{width:900,height:900}});
  await reference.setContent(html);
  for(const width of [520,390,320])for(const scheme of ['light','dark']){
   await reference.locator('#lab').evaluate(el=>el.open=true);
   await reference.locator('#width').selectOption(String(width));await reference.locator('#theme').selectOption(scheme);
   await reference.locator('#lab').evaluate(el=>el.open=false);
   await reference.locator('.composer').screenshot({path:`${out}/approved-field-${width}-${scheme}.png`});
   await reference.locator('#config').click();await reference.locator('#panel').screenshot({path:`${out}/approved-options-${width}-${scheme}.png`});
   await reference.keyboard.press('Escape');
  }
  await reference.close();
 }
 page=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const mutations=[],errors=[];let choosers=0;
 page.on('pageerror',error=>errors.push(error.message));page.on('filechooser',()=>choosers++);
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')mutations.push(request.postDataJSON().action)});
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 const input=page.getByRole('textbox',{name:'Message Amplifier'}),options=page.getByRole('button',{name:'Model and reasoning settings',exact:true}),menu=page.getByRole('dialog',{name:'Chat options',exact:true}),add=page.getByRole('button',{name:'Add attachments',exact:true}),addMenu=page.getByRole('dialog',{name:'Add to your message',exact:true});
 const overview=async()=>{
  await expect(menu.getByRole('button',{name:'Choose model',exact:true})).toBeVisible();
  assert.deepEqual(await menu.locator('[data-composer-option]:visible').evaluateAll(nodes=>nodes.map(n=>n.dataset.composerOption)),['model','effort','provider','bundle']);
  assert.equal(await menu.locator('select:visible,input:visible').count(),0,'Overview must not regress to the old all-settings form');
 };
 const measure=async()=>menu.evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,viewport:{width:innerWidth,height:innerHeight},overflow:el.scrollWidth>el.clientWidth+1,buttons:[...el.querySelectorAll('button')].filter(n=>n.getClientRects().length).map(n=>({label:n.getAttribute('aria-label'),width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height}))}});
 const appearance=async(scheme,interfaceDetail)=>{
  const revision=await page.evaluate(async({scheme,interfaceDetail})=>{const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,scheme,interfaceDetail}}});if(!prepared.accepted)throw Error(prepared.error);const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});if(!applied.accepted)throw Error(applied.error);return applied.result.revision;},{scheme,interfaceDetail});
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().revision)).toBe(revision);
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
 };
 await input.fill('Keep this commentary while choosing settings.');
 for(const width of [900,520,390,320]){
  await page.setViewportSize({width,height:844});
  for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed']){
   await appearance(scheme,detail);await options.click();await overview();
   await expect(menu.getByRole('button',{name:'Choose model',exact:true})).toBeFocused();
   const geometry=await measure();assert.equal(geometry.overflow,false);assert.ok(geometry.height<410,'Overview must stay compact, not fill the phone');assert.ok(geometry.y>=0&&geometry.y+geometry.height<=844);
   for(const target of geometry.buttons)assert.ok(target.width>=44&&target.height>=44,JSON.stringify(target));
   evidence.geometry.push({width,scheme,detail,...geometry});
   if(detail==='standard'){await page.screenshot({path:`${out}/options-${width}-${scheme}.png`});await menu.screenshot({path:`${out}/options-panel-${width}-${scheme}.png`});}
   for(const [name,selector] of [['Choose model','#chat-model'],['Choose reasoning effort','#chat-effort'],['Choose provider','#chat-provider'],['Choose agent bundle','#composer-options-bundle']]){
    const row=menu.getByRole('button',{name,exact:true});await row.click();await expect(menu.locator(selector)).toBeVisible();
    await menu.getByRole('button',{name:'Back to chat options',exact:true}).click();await expect(row).toBeFocused();await overview();
   }
   await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();await expect(input).toHaveDraft('Keep this commentary while choosing settings.');
  }
 }
 // Same boundary rejects deliberate exposure of the previous dense settings panel.
 await options.click();await menu.locator('[data-composer-page="model"]').evaluate(el=>el.hidden=false);
 await assert.rejects(overview);evidence.negativeControl='All-settings exposure rejected by the same overview assertion';
 await menu.locator('[data-composer-page="model"]').evaluate(el=>el.hidden=true);await overview();await page.keyboard.press('Escape');
 await page.emulateMedia({reducedMotion:'reduce'});
 await add.click();await expect(addMenu).toBeVisible();await expect(addMenu.getByRole('button',{name:'Attach files',exact:true})).toBeFocused();assert.equal(choosers,0);
 await expect(addMenu.getByRole('button',{name:'Chat access',exact:true})).toBeDisabled();
 await page.screenshot({path:out+'/add-320.png'});await page.keyboard.press('Escape');await expect(add).toBeFocused();
 await add.click();const imageChooser=page.waitForEvent('filechooser');await addMenu.getByRole('button',{name:'Add an image',exact:true}).click();const image=await imageChooser;assert.equal(await image.element().getAttribute('accept'),'image/*');await image.setFiles([]);await expect(input).toHaveDraft('Keep this commentary while choosing settings.');
 await add.click();const filesChooser=page.waitForEvent('filechooser');await addMenu.getByRole('button',{name:'Attach files',exact:true}).click();const files=await filesChooser;assert.equal(await files.element().getAttribute('accept'),'');await files.setFiles([{name:'synthetic-notes.txt',mimeType:'text/plain',buffer:Buffer.from('Fixture notes')},{name:'synthetic-budget.csv',mimeType:'text/csv',buffer:Buffer.from('cost\n20')}]);
 await expect(page.getByRole('button',{name:'Remove synthetic-notes.txt'})).toBeVisible();await expect(input).toHaveDraft('Keep this commentary while choosing settings.');
 await options.click();await overview();
 const menuBounds=await menu.boundingBox(),addBounds=await add.boundingBox(),primaryBounds=await page.locator('[data-part="composer-primary-action"]').boundingBox();
 assert.ok(menuBounds.y+menuBounds.height<=Math.min(addBounds.y,primaryBounds.y),'Menu must leave both composer action buttons reachable');
 await add.click();await expect(menu).toHaveCount(0);await expect(addMenu).toBeVisible();await page.keyboard.press('Escape');
 assert.equal(mutations.filter(a=>['conversation.send','conversation.configure','access.set','permissions.save','session.create'].includes(a)).length,0,'Menu navigation grants nothing, starts no chat and sends nothing');
 const dom=await page.evaluate(()=>window.amplifier.getState().renderedView);assert.ok(!JSON.stringify(dom).includes('Keep this commentary'));assert.ok(!JSON.stringify(dom).includes('synthetic-notes.txt'));
 const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setDeviceMetricsOverride',{width:400,height:500,deviceScaleFactor:2,mobile:false});await options.click();await overview();assert.equal((await measure()).overflow,false);await page.keyboard.press('Escape');await expect(options).toBeFocused();await cdp.send('Emulation.clearDeviceMetricsOverride');await cdp.detach();
 await page.reload();await input.waitFor();await expect(input).toHaveDraft('Keep this commentary while choosing settings.');await expect(page.getByRole('button',{name:'Remove synthetic-notes.txt'})).toBeVisible();
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();
 await input.fill('Retain draft through Access');await add.click();await addMenu.getByRole('button',{name:'Chat access',exact:true}).click();await expect(addMenu).toHaveCount(0);
 await expect(page.locator('.a-chat-pages')).toBeVisible();await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(input).toHaveDraft('Retain draft through Access');await expect(add).toBeFocused();
 assert.equal(mutations.filter(a=>a==='conversation.send').length,1);assert.equal(mutations.filter(a=>['access.set','permissions.save'].includes(a)).length,0);assert.deepEqual(errors,[]);
 evidence.add={choosers,accessHandoffWithoutGrant:true,draftAndFilesRetained:true};evidence.zoom='200% equivalent CSS viewport/DPR reflow, not physical browser zoom';
 await writeFile(out+'/fidelity.json',JSON.stringify(evidence,null,2));console.log('PASS approved Compact A disclosure: 4 widths, 3 detail levels, both schemes, Back/focus/escape, 44px, add/chooser/access, draft retention, negative control.');
}catch(error){await mkdir(out,{recursive:true});evidence.failure=String(error.stack||error);await writeFile(out+'/fidelity.json',JSON.stringify(evidence,null,2));await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error;
}finally{await browser?.close();if(fixture.exitCode===null&&fixture.signalCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');await exited;}}
