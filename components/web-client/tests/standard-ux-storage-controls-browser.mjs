// Four input contexts; actual review/expiry/deletion against disposable synthetic history only.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned AMPLIFIER_TEST_ARTIFACTS is required');
await mkdir(out,{recursive:true});
const variants=[['desktop-pointer',1280,950,'pointer'],['desktop-keyboard',1280,950,'keyboard'],['narrow-touch',390,844,'touch'],['narrow-keyboard',390,844,'keyboard']];
const results=[];
for(const [id,width,height,mode] of variants.filter(([id])=>!process.env.AMPLIFIER_TEST_VARIANTS||process.env.AMPLIFIER_TEST_VARIANTS.split(',').includes(id))){
 let fixture,browser,page,logs='';const row={id,status:'pending',context:'synthetic-storage-cleanup',limits:['Keyboard activation uses explicit focus plus Enter; full sequential Tab traversal not claimed.','Only disposable synthetic history; no real data or provider execution.','Bounded filesystem traversal and protected active owners require independent backend evidence.']},errors=[],external=[],actions=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/storage_history_ui_server.py'],{env:{...process.env,AMPLIFIER_TEST_DUPLICATE_TITLES:'1'}});
  fixture.stderr.on('data',chunk=>logs+=chunk);
  const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data)}}catch{}})});
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width,height},hasTouch:mode==='touch',isMobile:mode==='touch',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['http:','https:'].includes(url.protocol)&&url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort()}return route.continue()});
  const activate=async control=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press('Enter')}else if(mode==='touch')await control.tap();else await control.click()};
  await page.goto(boot.url);await page.waitForSelector('#amp-one');
  const settings=page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/});
  if(!await settings.isVisible())await activate(page.getByRole('button',{name:'Open navigation',exact:true}));
  await activate(settings);await expect(page.locator('.a-settings-experience')).toBeVisible();
  await page.waitForFunction(()=>{const el=document.querySelector('.a-settings-experience');return el&&el.dataset.compact===String((el.closest('.a-overlay')||el).clientWidth<960)});
  const panel=page.locator('.a-settings-experience');
  for(let depth=0;await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true';depth++){
   if(depth>=6)throw Error('Settings Back did not reach index');const previous=await panel.getAttribute('data-settings-route');
   await activate(panel.locator('.a-settings-mobile-head button').first());await expect(panel).not.toHaveAttribute('data-settings-route',previous);
  }
  await activate(page.locator('[data-settings-section="privacy"]'));
  await activate(page.locator('.a-settings-page-content:not([hidden]) [data-settings-destination="storage"]'));
  await expect(panel).toHaveAttribute('data-settings-page','storage');
  const storage=page.getByRole('region',{name:'Storage usage'}),nav=storage.getByRole('navigation',{name:'Storage navigation'});
  const state=async()=>{const response=await page.request.get(boot.url+'/api/fixture/history');assert.ok(response.ok());return response.json()};
  const initial=await state();assert.deepEqual(initial.exists,[true,true]);assert.equal(initial.projectExists,true);
  await expect(storage.locator('.a-storage-categories li')).toHaveCount(6);
  await expect(storage).toContainText('Deletion always starts with a review.');
  await activate(storage.getByRole('button',{name:/^Session data /}));
  await storage.getByRole('searchbox',{name:'Filter storage'}).fill('Shared CLI');
  await activate(storage.getByRole('button',{name:'Shared CLI and Unified history',exact:true}));
  await activate(storage.getByRole('button',{name:initial.project,exact:true}));
  await activate(storage.getByRole('button',{name:'sessions',exact:true}));
  await activate(storage.getByRole('button',{name:'Review cleanup for '+initial.ids[0],exact:true}));
  const review=storage.locator('.a-chat-delete'),remove=review.getByRole('button',{name:'Delete permanently',exact:true});
  await expect(remove).toBeEnabled();await expect(review).toContainText('This cannot be undone.');await expect(review).toContainText('Same saved title');
  await expect(review.locator('[data-part="deletion-review-identity"]')).toContainText(initial.workspace);
  await activate(review.getByText('Exact data being removed',{exact:true}));
  await expect(review.locator('code')).toHaveText(initial.ids[0]);
  assert.deepEqual((await state()).exists,[true,true]);
  assert.equal(actions.filter(value=>value.action==='storage.historyDelete').length,0);
  const previewAction=actions.filter(value=>value.action==='storage.historyDeletePreview').at(-1);
  assert.deepEqual(previewAction.args,{project:initial.project,nativeId:initial.ids[0]});
  await activate(review.getByRole('button',{name:'Keep session data',exact:true}));assert.deepEqual((await state()).exists,[true,true]);
  await activate(storage.getByRole('button',{name:'Review cleanup for '+initial.ids[1],exact:true}));await expect(remove).toBeEnabled();
  await expect(review).toContainText('Same saved title');await activate(review.getByText('Exact data being removed',{exact:true}));await expect(review.locator('code')).toHaveText(initial.ids[1]);
  await activate(review.getByRole('button',{name:'Keep session data',exact:true}));
  await activate(storage.getByRole('button',{name:'Review cleanup for '+initial.ids[0],exact:true}));await expect(remove).toBeEnabled();
  const expired=await page.request.post(boot.url+'/api/fixture/history/expire-review');assert.ok(expired.ok());assert.ok((await expired.json()).expired>0);
  await activate(remove);await expect(review.getByRole('alert')).toContainText('preview expired');
  assert.deepEqual((await state()).exists,[true,true]);await expect(remove).toHaveCount(0);
  await activate(review.getByRole('button',{name:'Review again',exact:true}));await expect(remove).toBeEnabled();
  const accepted=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='storage.historyDelete');
  await activate(remove);const response=await accepted;assert.ok(response.ok());const receipt=await response.json();assert.equal(receipt.accepted,true);
  await expect.poll(async()=>(await state()).exists).toEqual([false,true]);assert.equal((await state()).projectExists,true);
  const completion=receipt.result?.cleanupPending?'The sessions were removed. Some file cleanup is still pending.':'Session data deleted. Project working files were kept.';
  await expect(storage.getByRole('status').filter({hasText:completion})).toBeVisible();
  const deleted=actions.filter(value=>value.action==='storage.historyDelete');assert.equal(deleted.length,2);assert.equal(deleted[0].args.nativeId,initial.ids[0]);assert.equal(deleted[1].args.nativeId,initial.ids[0]);assert.notEqual(deleted[0].args.confirmationToken,deleted[1].args.confirmationToken);
  await activate(nav.getByRole('button',{name:'Storage',exact:true}));await expect(storage.locator('.a-storage-categories li')).toHaveCount(6);
  assert.deepEqual(actions.filter(value=>['conversation.send','session.warm'].includes(value.action)),[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:out+'/'+id+'.png'});
  row.status='passed';row.claims=[{workflow:'storage.review',expectedIndices:[0,1]},{workflow:'storage.delete',expectedIndices:[0,1,2,3]},{workflow:'privacy.cleanup',expectedIndices:[0,2]}];row.partial=[];row.receipt={duplicateTitlesDistinguished:true,expiredPreviewRejected:true,exactOneSessionRemoved:true,otherSessionAndProjectRetained:true};
 }catch(error){row.status='failed';row.error=String(error);if(page)try{await page.screenshot({path:out+'/'+id+'-failure.png'})}catch{}}
 finally{
  try{await browser?.close()}catch(error){row.cleanupError=String(error);row.status='failed'}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(out+'/'+id+'-fixture.log',logs);results.push(row);await writeFile(out+'/results.json',JSON.stringify({schemaVersion:1,results},null,2))}
 }
}
console.log(JSON.stringify({results}));if(results.some(row=>row.status!=='passed'))process.exitCode=1;
