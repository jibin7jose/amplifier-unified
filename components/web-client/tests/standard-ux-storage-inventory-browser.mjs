import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public storage inventory controls; all inspected files belong to this fixture.
import './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifacts required');await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/readiness102_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;const calls=[],reads=[],errors=[],checks=[];fixture.stderr.on('data',d=>logs+=d);
const unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
const bytes=n=>n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KB`:n<1073741824?`${(n/1048576).toFixed(1)} MB`:`${(n/1073741824).toFixed(2)} GB`;
const size=row=>row.pending?(row.bytes?bytes(row.bytes)+' so far':'Measuring…'):row.bytes==null?'Not measured':(row.incomplete?'At least ':'')+bytes(row.bytes);
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Storage fixture timed out')),20000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',d=>{text+=d;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions')calls.push(unwrap(r.postDataJSON()))});
 page.on('response',async response=>{if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return;const body=unwrap(response.request().postDataJSON());if(body?.action!=='storage.inspect')return;try{const value=await response.json();if(value.accepted&&value.result)reads.push({args:body.args||{},result:value.result})}catch{}});
 const state=()=>page.evaluate(()=>window.amplifier.getState()),info=async()=>(await page.request.get(url+'/api/fixture/readiness')).json();
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Private storage inspection draft');const initial=await state(),sid=initial.selectedSessionId,chat=initial.sessions.find(r=>r.id===sid),before=await info(),file=before.workspace+'/notes.txt',contents=await readFile(file,'utf8');
 await openSettingsPage(page,'storage');const storage=page.getByRole('region',{name:'Storage usage',exact:true});await expect(storage.locator('.a-storage-categories li')).toHaveCount(6);
 await expect.poll(()=>reads.some(r=>!r.args.category&&!r.args.rootId&&!r.result.scanning)).toBe(true);const overview=reads.findLast(r=>!r.args.category&&!r.args.rootId&&!r.result.scanning).result;
 assert.deepEqual(overview.rows.map(r=>r.id).sort(),['backups','cache','other','runtime','sessions','workspaces']);assert.equal(await storage.locator('.a-storage-items').count(),0);
 for(const row of overview.rows){const item=storage.locator('.a-storage-categories li').filter({has:page.getByRole('button',{name:new RegExp('^'+row.label+' ')})});await expect(item.locator('.a-storage-size')).toHaveText(size(row))}
 const breadcrumb=()=>storage.getByRole('navigation',{name:'Storage navigation',exact:true});
 for(const row of overview.rows){
  await storage.locator('.a-storage-categories').getByRole('button',{name:new RegExp('^'+row.label+' ')}).click();await expect.poll(()=>reads.some(r=>r.args.category===row.id&&!r.args.rootId&&!r.result.scanning)).toBe(true);
  const receipt=reads.findLast(r=>r.args.category===row.id&&!r.args.rootId&&!r.result.scanning).result;await expect(storage.locator('.a-storage-heading h3')).toHaveText(receipt.label);await expect(storage.locator('.a-storage-total strong')).toHaveText(size(receipt));assert.ok(await storage.locator('.a-storage-items li').count()<=5);
  if(['runtime','cache','other'].includes(row.id))await expect(storage.getByRole('button',{name:/Permanently delete|Delete permanently|Review cleanup/})).toHaveCount(0);
  await breadcrumb().getByRole('button',{name:'Storage',exact:true}).click();await expect(storage.locator('.a-storage-categories li')).toHaveCount(6);
 }
 checks.push('All six public categories show exact returned measurements/unknown markers, protected categories expose no deletion, and every category returns through Storage breadcrumb.');
 await storage.getByRole('button',{name:/^Workspace files /}).click();await expect(storage.locator('.a-storage-items li')).toHaveCount(5);await expect(storage.locator('.a-storage-items li').first()).toContainText('Storage example 6');await storage.getByRole('combobox',{name:'Sort storage',exact:true}).selectOption('name');await expect(storage.locator('.a-storage-items li').first()).toContainText('Cleanup demo');await storage.getByRole('combobox',{name:'Sort storage',exact:true}).selectOption('size');await storage.getByRole('button',{name:/^Show more/}).click();await expect(storage.locator('.a-storage-items li')).toHaveCount(8);
 await storage.getByRole('searchbox',{name:'Filter storage',exact:true}).fill('Cleanup demo');await expect(storage.locator('.a-storage-items li')).toHaveCount(1);await storage.getByRole('button',{name:'Cleanup demo',exact:true}).click();await expect(storage.locator('.a-storage-total code')).toHaveText(before.workspace);
 await storage.getByRole('button',{name:'nested',exact:true}).click();await expect(storage.locator('.a-storage-total code')).toHaveText(before.workspace+'/nested');await expect(storage.locator('.a-storage-items')).toContainText('data.txt');await expect.poll(()=>reads.some(r=>r.args.path==='nested'&&r.args.rootId)).toBe(true);const nestedRead=reads.findLast(r=>r.args.path==='nested');assert.ok(nestedRead);assert.ok(nestedRead.args.rootId);
 const count=reads.length;await storage.getByRole('button',{name:'Refresh storage',exact:true}).click();await expect.poll(()=>reads.length>count&&reads.at(-1).args.path==='nested'&&!reads.at(-1).result.scanning).toBe(true);await breadcrumb().getByRole('button',{name:'Cleanup demo',exact:true}).click();await expect(storage.locator('.a-storage-total code')).toHaveText(before.workspace);
 assert.ok(reads.every(r=>r.args.progressive===true));assert.ok(reads.filter(r=>r.args.path).every(r=>r.args.rootId));assert.deepEqual(calls.filter(r=>/delete|backupDelete|conversation.send|session.warm/i.test(r.action)),[]);
 await page.setViewportSize({width:390,height:844});assert.ok(await page.locator('#amp-one').evaluate(e=>e.scrollWidth<=e.clientWidth));await page.getByRole('button',{name:'Close settings',exact:true}).click();await expect(composer).toHaveDraft('Private storage inspection draft');assert.equal((await state()).selectedSessionId,sid);assert.deepEqual((await state()).sessions.find(r=>r.id===sid).messages,chat.messages);assert.deepEqual(await info(),before);assert.equal(await readFile(file,'utf8'),contents);assert.deepEqual(errors,[]);
 checks.push('Public sort/filter/Show more and exact nested folder/breadcrumb/Refresh stay scoped; browsing has no deletion/send and keeps selected saved messages, private draft and real fixture bytes.');
 await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks,requests:reads.map(r=>r.args),limits:['Real isolated filesystem and actual public controls. Shared-load functional observations only.','Progressive request scope and bounded displayed rows are asserted; no filesystem syscall proof that overview measurement avoids all closed-tree scans.','No active-resource deletion or arbitrary OS permission enforcement.']},null,2)+'\n');await page.screenshot({path:out+'/storage-inventory.png'});
}catch(e){failure=e;await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});throw e}
finally{const cleanup=[];try{await browser?.close()}catch(e){cleanup.push(e)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(e){cleanup.push(e)}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.log',logs)}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Storage cleanup failed')}
