// Production frontend and host with two real browser clients and synthetic data.
// Run after the parent's normal frontend build. No provider or live-user calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance lifecycle fixture'});
let browser,failure;
const state=page=>page.evaluate(()=>window.amplifier.getState());
const action=(page,name,args={})=>page.evaluate(async([name,args])=>{
 const receipt=await window.amplifier.dispatch(name,args);
 if(receipt.accepted===false)throw Error(receipt.error||name+' rejected');
 return receipt;
},[name,args]);
const read=async(page,id)=>(await action(page,'theme.read',{id})).result;
const list=async page=>(await action(page,'theme.list')).result.items;
const metadata=css=>JSON.parse(css.slice('/* amplifier-appearance: '.length,css.indexOf(' */\n')));
async function downloadedCss(page,button){
 const pending=page.waitForEvent('download');
 await button.click();
 const download=await pending,stream=await download.createReadStream(),chunks=[];
 assert.ok(stream,'The selected entry must produce downloadable bytes');
 for await(const chunk of stream)chunks.push(chunk);
 return Buffer.concat(chunks).toString('utf8');
}
async function upload(page,css,name='received.amplifier.css'){
 const response=page.waitForResponse(result=>result.url().endsWith('/api/actions')&&result.request().postDataJSON()?.action==='theme.import');
 await page.locator('#theme-import').setInputFiles({name,mimeType:'text/css',buffer:Buffer.from(css,'utf8')});
 const result=await response;assert.ok(result.ok(),'The portable upload must be accepted');
 await expect(page.getByText(/added to your library\. Select it to preview\./)).toBeVisible();
}
const editor=page=>page.locator('.a-settings-page-content:not([hidden]) .a-appearance-advanced');
const field=(page,id)=>editor(page).locator('#'+id);
const button=(page,name)=>page.getByRole('button',{name,exact:true});
async function editEntry(page,name){
 await button(page,'Preview '+name).click();
 await button(page,'Edit '+name).click();
 await expect(field(page,'theme-css')).toBeVisible();
}

try{
 const url=await new Promise((resolve,reject)=>{
  let output='';
  const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const errors=[];
 const options={viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}};
 const page=await browser.newPage(options),peer=await browser.newPage(options);
 for(const client of [page,peer])client.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);
 await action(page,'session.create');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this conversation draft.');
 const selected=(await state(page)).selectedSessionId;
 await peer.goto(url);await showFixtureConversation(peer);
 assert.notEqual((await state(page)).client.id,(await state(peer)).client.id,'The second browser must be a distinct client');
 await openSettingsPage(page,'custom-appearance');
 const before={...(await state(page)).theme};

 // UTF-8 BOM + CRLF + Unicode metadata must win over the upload filename.
 const meta={name:'Ocean',description:'Océan portable colors',author:'Example author'};
 const imported='\ufeff/* amplifier-appearance: '+JSON.stringify(meta)+' */\r\n#amp-one{--a-accent:#ad2233}\r\n';
 await upload(page,imported,'unrelated-filename.css');
 await expect(button(page,'Preview Ocean')).toBeVisible();
 assert.deepEqual((await state(page)).theme,before,'Import must not apply');
 const ocean=(await list(page)).find(row=>row.source==='saved'&&row.name==='Ocean');
 assert.ok(ocean);assert.equal(ocean.description,meta.description);
 await editEntry(page,'Ocean');
 assert.deepEqual((await state(page)).theme,before,'The library card is a local preview');
 assert.equal((await state(peer)).view.themePreview||false,false,'Preview cannot affect the peer');
 const original=await downloadedCss(page,button(page,'Export Ocean'));
 assert.deepEqual(metadata(original),meta);
 assert.equal(original.split('/* amplifier-appearance: ').length-1,1);

 // Keep the exact selected target, fingerprint, name, description and CSS
 // together even while main.jsx receives a different committed theme.
 const dirty={name:'Ocean edited',description:'My local draft description',css:'#amp-one{--a-accent:#765abc}'};
 await field(page,'theme-name').fill(dirty.name);
 await field(page,'theme-description').fill(dirty.description);
 await field(page,'theme-css').fill(dirty.css);
 await expect.poll(async()=>(await state(page)).view.themeDraftName).toBe(dirty.name);
 await button(page,'Preview').click();
 const accent=()=>page.locator('#amp-one').evaluate(node=>getComputedStyle(node).getPropertyValue('--a-accent').trim());
 await expect.poll(accent,{message:'The actual renderer must paint the authored draft, not the previously applied CSS'}).toBe('#765abc');
 assert.deepEqual((await state(page)).theme,before);
 const peerTheme={name:'Peer blue',css:'#amp-one{--a-accent:#2458ac}'};
 await action(peer,'theme.apply',peerTheme);
 await expect.poll(async()=>(await state(page)).theme.name).toBe(peerTheme.name);
 await expect.poll(async()=>(await state(page)).view.themePreview).toBe(false);
 await expect.poll(accent).toBe('#2458ac');
 for(const [id,value] of [['theme-name',dirty.name],['theme-description',dirty.description],['theme-css',dirty.css]])await expect(field(page,id)).toHaveValue(value);
 // Changing unrelated Settings sections retains the mounted local editor.
 await openSettingsPage(page,'privacy');
 await openSettingsPage(page,'custom-appearance');
 if(!await field(page,'theme-css').isVisible())await editor(page).locator('summary').click();
 await expect(field(page,'theme-css')).toHaveValue(dirty.css);
 // Settings closure and full tab reload retain all draft fields, including
 // the saved target/fingerprint. A successful later update proves that target.
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await openSettingsPage(page,'custom-appearance');
 await button(page,'Edit Ocean').click();
 for(const [id,value] of [['theme-name',dirty.name],['theme-description',dirty.description],['theme-css',dirty.css]])await expect(field(page,id)).toHaveValue(value);
 await page.reload();await showFixtureConversation(page);
 await openSettingsPage(page,'custom-appearance');
 await button(page,'Edit Ocean').click();
 for(const [id,value] of [['theme-name',dirty.name],['theme-description',dirty.description],['theme-css',dirty.css]])await expect(field(page,id)).toHaveValue(value);
 await expect.poll(async()=>(await state(page)).view.themePreview).toBe(false);
 await button(page,'Preview').click();
 await expect.poll(accent).toBe('#765abc');
 await button(page,'Update saved appearance').click();
 await expect(page.getByText('Library entry updated. Apply separately to change the current appearance.')).toBeVisible();
 const updated=await read(page,ocean.id);
 assert.equal(updated.id,ocean.id);assert.equal(updated.name,dirty.name);
 assert.equal(updated.description,dirty.description);assert.ok(updated.css.endsWith(dirty.css));
 assert.notEqual(updated.fingerprint,ocean.fingerprint);
 assert.deepEqual((await state(page)).theme,peerTheme,'Updating the library cannot apply or copy the peer payload');

 // The old content-addressed filename now owns the updated entry. Restoring
 // the original export allocates another entry instead of overwriting/failing.
 await upload(page,original,'old-export.amplifier.css');
 const restored=(await list(page)).find(row=>row.source==='saved'&&row.name==='Ocean');
 assert.ok(restored);assert.notEqual(restored.id,ocean.id);
 assert.equal((await read(page,restored.id)).css,original);
 assert.equal((await read(page,ocean.id)).fingerprint,updated.fingerprint);
 const count=(await list(page)).filter(row=>row.source==='saved').length;
 await upload(page,original,'same-export.css');
 assert.equal((await list(page)).filter(row=>row.source==='saved').length,count,'Identical reimport remains idempotent');

 // A polling refresh may show a newer fingerprint, but cannot replace the
 // old edit target's fingerprint or the unsaved draft.
 await editEntry(page,dirty.name);
 const retained={name:'Keep my unsaved draft',description:'Do not discard this description',css:'#amp-one{--a-accent:#885599}'};
 for(const [id,value] of [['theme-name',retained.name],['theme-description',retained.description],['theme-css',retained.css]])await field(page,id).fill(value);
 const remote=await action(peer,'theme.update',{id:updated.id,expectedFingerprint:updated.fingerprint,name:'Ocean remotely updated',description:'Changed by the second editor',css:'#amp-one{--a-accent:#887722}'});
 await expect(button(page,'Preview Ocean remotely updated')).toBeVisible({timeout:12000});
 await button(page,'Update saved appearance').click();
 await expect(page.getByRole('alert').filter({hasText:'This appearance changed.'})).toBeVisible();
 for(const [id,value] of [['theme-name',retained.name],['theme-description',retained.description],['theme-css',retained.css]])await expect(field(page,id)).toHaveValue(value);
 assert.equal((await read(page,updated.id)).fingerprint,remote.result.fingerprint);
 await button(page,'Delete '+dirty.name).click();
 const confirmation=page.getByRole('group',{name:'Delete appearance confirmation'});
 await expect(confirmation).toBeVisible();
 await button(page,'Confirm delete').click();
 await expect(page.getByRole('alert').filter({hasText:'This appearance changed.'})).toBeVisible();
 assert.equal((await read(page,updated.id)).fingerprint,remote.result.fingerprint);
 await button(page,'Cancel deletion').click();

 // Explicit re-selection refreshes the target. Deletion requires its visible
 // confirmation; Cancel has no effect, and Confirm leaves applied CSS intact.
 await editEntry(page,'Ocean remotely updated');
 await button(page,'Apply appearance').click();
 await expect.poll(async()=>(await state(page)).theme.name).toBe('Ocean remotely updated');
 const applied={...(await state(page)).theme};
 await button(page,'Delete Ocean remotely updated').click();
 await expect(confirmation).toBeVisible();
 assert.ok((await list(page)).some(row=>row.id===updated.id),'Opening confirmation cannot delete');
 await button(page,'Cancel deletion').click();
 assert.ok((await list(page)).some(row=>row.id===updated.id),'Cancel must preserve the entry');
 await button(page,'Delete Ocean remotely updated').click();
 await button(page,'Confirm delete').click();
 await expect(page.getByText('Deleted from your library. The applied appearance is unchanged.')).toBeVisible();
 assert.ok(!(await list(page)).some(row=>row.id===updated.id));
 assert.deepEqual((await state(page)).theme,applied);
 await expect.poll(async()=>(await state(peer)).theme).toEqual(applied);
 assert.deepEqual((await state(peer)).theme,applied);
 await field(page,'theme-name').fill('New local copy');
 await button(page,'Save as copy').click();
 await expect(button(page,'Preview New local copy')).toBeVisible();
 assert.deepEqual((await state(page)).theme,applied,'Save as copy cannot apply');

 // A saved editor also survives closure/reload; preview itself never resumes.
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await expect.poll(async()=>(await state(page)).view.themePreview).toBe(false);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this conversation draft.');
 assert.equal((await state(page)).selectedSessionId,selected);
 await page.reload();await showFixtureConversation(page);
 assert.deepEqual((await state(page)).theme,applied);
 assert.ok((await list(page)).some(row=>row.name==='New local copy'));
 assert.deepEqual(errors,[]);
 console.log('Appearance lifecycle passed: two-client draft/target preservation; computed authored preview CSS; close/reopen/reload persistence; UI edit/update/save-copy; stale update/delete conflicts; delete confirmation/cancel; applied CSS retention; BOM/CRLF metadata export/import; old-export collision and idempotent reimport; conversation preservation.');
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Appearance lifecycle fixture',close:[['Appearance lifecycle browser',()=>browser?.close()]],stop,originalError:failure})}