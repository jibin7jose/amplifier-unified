import {showFixtureConversation} from './browser-chat-pages.mjs';
// Actual synthetic file import and private backup through public Settings controls.
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
 let fixture,browser,page,logs='';const row={id,status:'pending',context:'synthetic-privacy-effects',limits:['Keyboard activation uses explicit focus plus Enter; full sequential Tab traversal not claimed.','Only disposable synthetic history; no real data or provider execution.','Actual synthetic import and private backup; no real credentials, repair, reset, provider or account effects.']},errors=[],external=[],actions=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/privacy_effects_ui_server.py']);
  fixture.stderr.on('data',chunk=>logs+=chunk);
  const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data)}}catch{}})});
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width,height},hasTouch:mode==='touch',isMobile:mode==='touch',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['http:','https:'].includes(url.protocol)&&url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort()}return route.continue()});
  const activate=async control=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press('Enter')}else if(mode==='touch')await control.tap();else await control.click()};
  await page.goto(boot.url);await showFixtureConversation(page);await page.waitForSelector('#amp-one');
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
  const privacy=async()=>{
   for(let depth=0;await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true';depth++){
    if(depth>=6)throw Error('Privacy Back did not reach index');const previous=await panel.getAttribute('data-settings-route');
    await activate(panel.locator('.a-settings-mobile-head button').first());await expect(panel).not.toHaveAttribute('data-settings-route',previous);
   }
   await activate(page.locator('[data-settings-section="privacy"]'));await expect(panel).toHaveAttribute('data-settings-page','privacy');
  };
  const observe=async()=>{const response=await page.request.get(boot.url+'/api/fixture/privacy-observations');assert.equal(response.status(),200);return response.json()};
  const initial=await observe();assert.equal(initial.originalPreserved,true);assert.deepEqual(initial.runtimeCalls,[]);assert.equal(initial.schedules,0);
  await privacy();await activate(page.locator('[data-settings-destination="history"]'));await expect(panel).toHaveAttribute('data-settings-page','history');
  let surface=page.locator('.a-settings-page-content:not([hidden])');
  await expect(surface).toContainText('Imported jobs do not restart automatically.');
  await surface.getByLabel('Conversation title (optional)',{exact:true}).fill('Imported synthetic privacy proof');
  const rows=[{role:'user',content:'Synthetic imported question'},{role:'assistant',content:'',tool_calls:[{id:'old-call',function:{name:'bash',arguments:'{}'}}]},{role:'tool',tool_call_id:'old-call',content:'Historical tool output, never execute'},{role:'assistant',content:'Synthetic imported answer'}];
  const payload={messages:rows,metadata:{bundle_name:'anchors',jobs:[{command:'must-not-run'}],schedules:[{enabled:true}],parent_id:'untrusted-parent'}};
  const chooser=page.waitForEvent('filechooser');await activate(surface.getByRole('button',{name:'Choose a conversation',exact:true}));
  await (await chooser).setFiles({name:'synthetic-conversation.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(payload))});
  await expect.poll(async()=>(await observe()).imported.length).toBe(1);
  const imported=await observe();const entry=imported.imported[0];assert.notEqual(entry.id,initial.originalId);assert.deepEqual(entry.messages,rows);assert.equal(entry.status,'stopped');assert.equal(entry.jobsReplayed,false);assert.equal(imported.originalPreserved,true);assert.equal(imported.schedules,0);assert.deepEqual(imported.runtimeCalls,[]);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(entry.id);
  assert.equal(actions.filter(action=>action.action==='history.importFile').length,1);
  await privacy();await activate(page.locator('[data-settings-destination="repair"]'));await expect(panel).toHaveAttribute('data-settings-page','repair');surface=page.locator('.a-settings-page-content:not([hidden])');
  await expect(surface).toContainText('Backups contain credentials and are not encrypted. Keep them private.');
  await activate(surface.getByRole('button',{name:'Create full backup',exact:true}));
  await expect.poll(async()=>(await observe()).backup!==null,{timeout:15000}).toBe(true);
  const completed=await observe();assert.equal(completed.backup.fileMode,0o600);assert.equal(completed.backup.directoryMode,0o700);assert.ok(completed.backup.bytes>0);assert.equal(completed.backup.hasDatabase,true);assert.equal(completed.backup.hasImportedTranscript,true);
  await expect(surface).toContainText('Saved backup: '+completed.backup.path);
  assert.deepEqual(completed.sessionIds,imported.sessionIds);assert.equal(completed.originalPreserved,true);assert.deepEqual(completed.imported,imported.imported);assert.deepEqual(completed.runtimeCalls,[]);assert.equal(completed.schedules,0);
  assert.equal(actions.filter(action=>action.action==='maintenance.backup').length,1);
  assert.deepEqual(actions.filter(value=>['maintenance.repair','maintenance.reset','conversation.send','session.warm'].includes(value.action)),[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:out+'/'+id+'.png'});
  row.status='passed';row.claims=[{workflow:'privacy.import',expectedIndices:[0,1,2]},{workflow:'privacy.backup',expectedIndices:[0,1,2,3]}];row.receipt={distinctImport:true,originalRetained:true,historicalToolRowsRetained:true,noExecution:true,noSchedules:true,backupFileMode:completed.backup.fileMode,backupDirectoryMode:completed.backup.directoryMode,backupContainsDatabaseAndImportedTranscript:true};
 }catch(error){row.status='failed';row.error=String(error);if(page)try{await page.screenshot({path:out+'/'+id+'-failure.png'})}catch{}}
 finally{
  try{await browser?.close()}catch(error){row.cleanupError=String(error);row.status='failed'}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(out+'/'+id+'-fixture.log',logs);results.push(row);await writeFile(out+'/results.json',JSON.stringify({schemaVersion:1,results},null,2))}
 }
}
console.log(JSON.stringify({results}));if(results.some(row=>row.status!=='passed'))process.exitCode=1;
