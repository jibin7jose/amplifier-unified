import {showFixtureConversation} from './browser-chat-pages.mjs';
// Read-only standard Privacy links and recovery/capture explanations, in four input contexts.
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
for(const [id,width,height,mode] of variants){
 let fixture,browser,page,logs='';const row={id,status:'pending',context:'synthetic-privacy-navigation',limits:['Keyboard activation uses explicit focus plus Enter; full sequential Tab traversal not claimed.','Only disposable synthetic history; no real data or provider execution.','Read-only navigation and explanations; no backup, import, capture, repair or reset effect.']},errors=[],external=[],actions=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/storage_history_ui_server.py']);
  fixture.stderr.on('data',chunk=>logs+=chunk);
  const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data)}}catch{}})});
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width,height},hasTouch:mode==='touch',isMobile:mode==='touch',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['http:','https:'].includes(url.protocol)&&url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort()}return route.continue()});
  const activate=async control=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press('Enter')}else if(mode==='touch')await control.tap();else await control.click()};
  await page.goto(boot.url);await showFixtureConversation(page,{navPinned:true});await page.waitForSelector('#amp-one');
  const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep this unsent privacy draft');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view?.draft)).toBe('Keep this unsent privacy draft');
  const settings=page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/});
  if(!await settings.isVisible())await activate(page.getByRole('button',{name:'Open navigation',exact:true}));
  await activate(settings);await expect(page.locator('.a-settings-experience')).toBeVisible();
  await page.waitForFunction(()=>{const el=document.querySelector('.a-settings-experience');return el&&el.dataset.compact===String((el.closest('.a-overlay')||el).clientWidth<960)});
  const panel=page.locator('.a-settings-experience');
  for(let depth=0;await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true';depth++){
   if(depth>=6)throw Error('Settings Back did not reach index');const previous=await panel.getAttribute('data-settings-route');
   await activate(panel.locator('.a-settings-mobile-head button').first());await expect(panel).not.toHaveAttribute('data-settings-route',previous);
  }
  const snapshot=()=>page.evaluate(()=>{const s=window.amplifier.getState();return {selected:s.selectedSessionId,sessions:(s.sessions||[]).map(r=>({id:r.id,title:r.title})),draft:s.view?.draft}});
  const before=await snapshot();
  const privacy=async()=>{
   for(let depth=0;await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true';depth++){
    if(depth>=6)throw Error('Privacy Back did not reach index');const previous=await panel.getAttribute('data-settings-route');
    await activate(panel.locator('.a-settings-mobile-head button').first());await expect(panel).not.toHaveAttribute('data-settings-route',previous);
   }
   await activate(page.locator('[data-settings-section="privacy"]'));await expect(panel).toHaveAttribute('data-settings-page','privacy');
  };
  const destinations=[['storage','Storage'],['workspaces','Files & folders'],['permissions','File access'],['desktop','Screen & computer access'],['history','Saved work'],['diagnostics','App diagnostic capture'],['repair','Backups & recovery']];
  const visited=[];
  for(const [destination,label] of destinations){
   await privacy();const link=page.locator('.a-settings-page-content:not([hidden]) [data-settings-destination="'+destination+'"]');await expect(link).toContainText(label);await activate(link);
   await expect(panel).toHaveAttribute('data-settings-page',destination);const surface=page.locator('.a-settings-page-content:not([hidden])');await expect(surface).toBeVisible();await expect.poll(async()=>(await surface.innerText()).trim().length,{message:'Destination '+destination+' has actual content'}).toBeGreaterThan(30);
   if(destination==='history'){
    await expect(surface).toContainText('Original transcript entries are preserved. Imported jobs do not restart automatically.');await expect(surface.getByRole('button',{name:'Choose a conversation',exact:true})).toBeVisible();
   }
   if(destination==='diagnostics'){
    await expect(surface).toContainText('App forwarding starts only after you save an enabled destination.');
    await expect(surface).toContainText('Saved records can contain private messages, instructions, tool inputs, and attachments.');
    await expect(surface).toContainText('Recognizable credentials are redacted.');
   }
   if(destination==='repair'){
    await expect(surface).toContainText('Backups contain credentials and are not encrypted. Keep them private.');
    await expect(surface.getByRole('button',{name:'Create full backup',exact:true})).toBeVisible();
    await expect(surface.getByRole('button',{name:'Repair runtime',exact:true})).toBeVisible();
   }
   visited.push(destination);
  }
  await privacy();assert.deepEqual(await snapshot(),before);
  const prohibited=['history.importFile','history.cleanup','history.delete','diagnostics.configure','maintenance.backup','maintenance.repair','maintenance.reset','conversation.send','session.warm'];
  assert.deepEqual(actions.filter(value=>prohibited.includes(value.action)),[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:out+'/'+id+'.png'});
  row.status='passed';row.claims=[{workflow:'privacy.links',expectedIndices:[0,1]},{workflow:'privacy.capture',expectedIndices:[0,2]},{workflow:'privacy.backup',expectedIndices:[0,2,3]}];
  row.partial=[{workflow:'privacy.links',expectedIndex:2,reason:'Seven outgoing Privacy links exercised; all other standard alternate entry points are not qualified by this harness.'},{workflow:'privacy.capture',expectedIndex:1,reason:'Warnings verified; actual payload redaction/private draft exclusion require separate backend capture evidence.'},{workflow:'privacy.backup',expectedIndex:1,reason:'Read-only recovery screen; no backup created or destination permissions measured.'}];row.receipt={visited,readOnly:true,savedChatStatePreserved:true};
 }catch(error){row.status='failed';row.error=String(error);if(page)try{await page.screenshot({path:out+'/'+id+'-failure.png'})}catch{}}
 finally{
  try{await browser?.close()}catch(error){row.cleanupError=String(error);row.status='failed'}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(out+'/'+id+'-fixture.log',logs);results.push(row);await writeFile(out+'/results.json',JSON.stringify({schemaVersion:1,results},null,2))}
 }
}
console.log(JSON.stringify({results}));if(results.some(row=>row.status!=='passed'))process.exitCode=1;
