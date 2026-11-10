// Public connection setup with synthetic keys/model discovery and no provider network effects.
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
 let fixture,browser,page,logs='';const row={id,status:'pending',context:'synthetic-connection-setup',providerEffects:false,limits:['Keyboard activation uses explicit focus plus Enter; native virtual keyboard and full Tab traversal not claimed.','Credentials and provider model discovery are synthetic; no real account or inference acceptance.','Optional endpoint validation is local form evidence, not actual service reachability.']},errors=[],external=[],actions=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/settings_collections_server.py']);
  fixture.stderr.on('data',chunk=>logs+=chunk);
  const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n')){if(/^http:\/\/127\.0\.0\.1:\d+$/.test(line.trim())){clearTimeout(timer);resolve({url:line.trim()})}}})});
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
  await activate(page.locator('[data-settings-section="ai"]'));
  const surface=page.locator('.a-settings-page-content:not([hidden]) [data-part="ai-connections"]');
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  await expect(surface.getByRole('button',{name:/OpenAI API.*one/})).toBeVisible();
  const before=(await state()).setup.providers;const ids=before.map(row=>row.id).sort();
  assert.ok(!JSON.stringify(await state()).includes('fixture-private-key'));
  await expect(surface).toContainText('Connections are stored on the Amplifier host. Existing conversations keep their settings.');
  await activate(surface.getByRole('button',{name:'Connect another service',exact:true}));
  for(const name of ['GitHub Copilot Subscription','ChatGPT Subscription','OpenAI API','Anthropic API','Google Gemini API','OpenAI Compatible API'])await expect(surface.getByRole('button',{name:new RegExp('^'+name)})).toBeVisible();
  assert.equal(actions.filter(value=>value.action==='providers.save').length,0);
  await activate(surface.getByRole('button',{name:'Back',exact:true}));assert.deepEqual((await state()).setup.providers.map(row=>row.id).sort(),ids);
  await activate(surface.getByRole('button',{name:/OpenAI API.*one/}));
  await activate(surface.getByText('Account and connection checks',{exact:true}));
  await expect(surface.getByText('fixtur…-key',{exact:true})).toBeVisible();await expect(surface.getByText('FIXTURE_KEY',{exact:true})).toBeVisible();
  await activate(surface.getByRole('button',{name:'Change credentials',exact:true}));
  const key=surface.getByLabel('API key',{exact:true});await expect(key).toBeEnabled();await expect(key).toHaveAttribute('type','password');
  const replacement='synthetic-key-middle-9876';await key.fill(replacement);
  const save=panel.getByRole('button',{name:'Save and check connection',exact:true});let refused=false;
  const handler=async route=>{const body=route.request().postDataJSON();if(!refused&&body?.action==='providers.save'){refused=true;return route.fulfill({status:503,json:{accepted:false,error:'Synthetic credential save refused'}})}return route.continue()};
  await page.route('**/api/actions',handler);await activate(save);
  await expect(surface.getByRole('alert')).toContainText('Synthetic credential save refused');
  assert.equal((await state()).setup.providers.find(row=>row.id==='one').credential.preview.masked,'fixtur…-key');
  assert.ok(!JSON.stringify(await state()).includes(replacement));
  await page.unroute('**/api/actions',handler);await activate(save);
  await expect(surface.getByText('Model list checked. No chat request was sent.',{exact:true})).toBeVisible();
  await activate(surface.getByRole('button',{name:'Back',exact:true}));
  await activate(surface.getByText('Account and connection checks',{exact:true}));
  await expect(surface.getByText('synthe…9876',{exact:true})).toBeVisible();await expect(surface.getByText(/^Private keys file \(keys.env\)/)).toBeVisible();
  const after=(await state()).setup.providers;assert.deepEqual(after.map(row=>row.id).sort(),ids);
  for(const identity of ['two','three'])assert.deepEqual(after.find(row=>row.id===identity).credential,before.find(row=>row.id===identity).credential);
  assert.ok(!JSON.stringify(await state()).includes(replacement));assert.ok(!(await page.content()).includes(replacement));
  const saves=actions.filter(value=>value.action==='providers.save');assert.equal(saves.length,2);
  for(const request of saves){assert.equal(request.args.id,'one');assert.equal(request.args.module,'provider-openai');assert.equal(request.args.scope,'global');assert.deepEqual(request.args.configurationTarget,{kind:'global'});assert.equal(request.args.apiKey,replacement);assert.equal(request.args.apiKeyEnv,undefined);assert.equal(request.args.useGitHubCli,undefined)}
  await activate(surface.getByRole('button',{name:'Back',exact:true}));await activate(surface.getByRole('button',{name:'Connect another service',exact:true}));
  await activate(surface.getByRole('button',{name:/^OpenAI Compatible API/}));
  const endpoint=surface.getByLabel('API base URL',{exact:true}),optional=surface.getByLabel('API key (optional)',{exact:true});
  await expect(endpoint).toBeEnabled();await endpoint.fill('not-a-url');await expect(save).toBeDisabled();
  await endpoint.fill('http://127.0.0.1:9/v1');await expect(optional).toHaveValue('');await expect(save).toBeEnabled();
  await expect(surface).toContainText('Leave blank if your local service does not need a key.');
  await activate(surface.getByRole('button',{name:'Back',exact:true}));await activate(surface.getByRole('button',{name:'Back',exact:true}));
  assert.deepEqual((await state()).setup.providers.map(row=>row.id).sort(),ids);assert.equal(actions.filter(value=>value.action==='providers.save').length,2);
  assert.deepEqual(actions.filter(value=>['conversation.send','providers.testMessage','providers.generateImage','session.warm'].includes(value.action)),[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:out+'/'+id+'.png'});
  row.status='passed';row.claims=[{workflow:'connections.catalog',expectedIndices:[0,1,2]},{workflow:'connections.key',expectedIndices:[0,1,2,3]},{workflow:'connections.endpoint',expectedIndices:[1,2]}];row.partial=[{workflow:'connections.endpoint',expectedIndex:0,reason:'Invalid/valid local URL affordance proved; no actual endpoint connection/reachability claim.'}];row.receipt={maskedKeyOnly:true,failedSaveRetainedOldKey:true,exactGlobalOwner:true,noAlternateCredentialSource:true,optionalKey:true};
 }catch(error){row.status='failed';row.error=String(error);if(page)try{await page.screenshot({path:out+'/'+id+'-failure.png'})}catch{}}
 finally{
  try{await browser?.close()}catch(error){row.cleanupError=String(error);row.status='failed'}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(out+'/'+id+'-fixture.log',logs);results.push(row);await writeFile(out+'/results.json',JSON.stringify({schemaVersion:1,results},null,2))}
 }
}
console.log(JSON.stringify({results}));if(results.some(row=>row.status!=='passed'))process.exitCode=1;
