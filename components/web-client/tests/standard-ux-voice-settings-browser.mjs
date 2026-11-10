import {openSettingsSection} from './browser-settings.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('An owned AMPLIFIER_TEST_ARTIFACTS directory is required');
const evidence=new StandardUxEvidence(output,{baseline:'1d993e0bb32bc90b75fc66f56f1b9014c2d63f87',sourceReceipt:'execution-source.json',data:'synthetic-standard',runtime:'Actual voice Settings/storage; synthetic sample renderer, no provider',permission:'No microphone or external audio acceptance',timings:'Shared DTU functional observations, not controlled performance'});
const variants=[{id:'desktop-pointer',width:1366,height:900,mode:'pointer'},{id:'desktop-keyboard',width:1366,height:900,mode:'keyboard'},{id:'narrow-touch',width:390,height:844,mode:'touch'},{id:'narrow-keyboard',width:390,height:844,mode:'keyboard'}].filter(row=>!process.env.STANDARD_UX_VARIANTS||process.env.STANDARD_UX_VARIANTS.split(',').includes(row.id));
await mkdir(output,{recursive:true});let failed=false;
for(const variant of variants){
 let fixture,browser,page,logs='',releaseHeld;
 const errors=[],calls=[],external=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/voice_settings_ui_server.py'],{stdio:['ignore','pipe','pipe']});fixture.stderr.on('data',chunk=>logs+=chunk);
  const port=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Preferences fixture startup timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Preferences fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
  const url='http://127.0.0.1:'+port;
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width:variant.width,height:variant.height},hasTouch:variant.mode==='touch',isMobile:variant.mode==='touch',colorScheme:'light',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
  await page.route('**/*',route=>{const parsed=new URL(route.request().url());if(['http:','https:'].includes(parsed.protocol)&&parsed.hostname!=='127.0.0.1'){external.push(parsed.origin);return route.abort()}return route.continue()});

  evidence.page=page;
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  const presentation=()=>page.evaluate(()=>window.amplifier.getShellState()?.effectiveComposition.presentation);
  const act=async(label,control)=>evidence.action(label,async()=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(variant.mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press(await control.evaluate(el=>el.matches('input[type=checkbox],input[type=radio]'))?'Space':'Enter')}else if(variant.mode==='touch')await control.tap();else await control.click()});
  const section=async id=>{
   await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
   await expect(page.locator('#amp-one')).toBeVisible();
   await openSettingsSection(page,id,{activate:act});
  };
  const probe=async(id,workflows,run)=>{const record=await evidence.probe(variant.id+'.'+id,workflows,async()=>{await run();return {variant,...await evidence.snapshot(page,variant.id+'.'+id,'#amp-one')}});if(record.status!=='passed')throw Error('Dependent preference checks stopped after '+id)};
  const inspect=async()=>await(await page.request.get(url+'/fixture/voice')).json();
  const voicePage=()=>page.locator('.a-voice-settings');
  await probe('saved-voice',['voice.setup'],async()=>{
   await evidence.action('Open synthetic voice configuration',()=>page.goto(url));await section('voice');
   await expect(voicePage().getByText('Using the OpenAI API key on this host.',{exact:true})).toBeVisible();
   await expect(page.locator('.a-voice-current')).toContainText('retired-fixture-voice');
   await expect(page.locator('.a-voice-current')).toContainText('unavailable');
   await expect(page.getByRole('button',{name:'Preview voice',exact:true})).toBeDisabled();
   evidence.assertion('No preview on Settings entry',(await inspect()).samples,[]);
  });
  await probe('select-preview',['voice.setup'],async()=>{
   await act('Open speaking voice choices',page.getByRole('button',{name:'Choose a voice',exact:true}));
   await evidence.action('Filter voice choices',()=>page.getByRole('searchbox',{name:'Find a voice',exact:true}).fill('Cedar'));
   await act('Choose Cedar explicitly',page.getByRole('radio',{name:/^Cedar /}));
   await expect.poll(async()=>(await state()).settings.voiceName).toBe('cedar');
   await expect(page.locator('.a-voice-current')).toContainText('Cedar');
   evidence.assertion('Selecting a voice does not create sample',(await inspect()).samples,[]);
   await act('Request explicit voice preview',page.getByRole('button',{name:'Preview voice',exact:true}));
   await expect(page.locator('.a-voice-sample audio')).toBeVisible();
   evidence.assertion('Exactly one sample for selected voice',(await inspect()).samples,[{model:'gpt-live-1',voice:'cedar'}]);
  });
  await probe('private-key',['voice.setup'],async()=>{
   await act('Open voice connection',page.getByText('Change voice connection',{exact:true}));
   await act('Choose separate key',page.getByRole('radio',{name:'Use a separate key',exact:true}));
   const key=page.getByLabel('OpenAI API key',{exact:true});await evidence.action('Enter synthetic private voice key',()=>key.fill('synthetic-private-voice-key'));
   await expect(key).toHaveAttribute('type','password');
   const handler=async route=>{if(route.request().postDataJSON()?.action==='voice.configure')return route.fulfill({status:409,json:{accepted:false,error:'Synthetic rejected credential save'}});await route.continue()};await page.route('**/api/actions',handler);
   await act('Try rejected save',page.getByRole('button',{name:'Save voice connection',exact:true}));await expect(voicePage().getByRole('alert')).toContainText('Could not save voice key. Your entry is kept; try again.');await expect(key).toHaveValue('synthetic-private-voice-key');
   await page.unroute('**/api/actions',handler);await act('Save separate key explicitly',page.getByRole('button',{name:'Save voice connection',exact:true}));
   await expect(voicePage().getByText('Using your separate voice API key.',{exact:true})).toBeVisible();await expect(key).toHaveValue('');
   const data=await inspect();evidence.assertion('Actual private storage has intended key',data.storedKeyMatches);evidence.assertion('Shared state excludes secret',data.stateContainsSyntheticKey,false);
   await evidence.action('Reload saved voice configuration',()=>page.reload());await section('voice');await expect(voicePage().getByText('Using your separate voice API key.',{exact:true})).toBeVisible();await expect(page.locator('.a-voice-current')).toContainText('Cedar');
  });
  await probe('fallback',['voice.fallback'],async()=>{
   await evidence.action('Choose supported voice model via native select; picker gesture unqualified',()=>page.getByLabel('Voice model',{exact:true}).selectOption('gpt-realtime-2.1'));
   const interruptions=page.getByRole('checkbox',{name:'Let me interrupt by speaking',exact:true});await expect(interruptions).toBeChecked();await act('Disable interruption',interruptions);await expect.poll(async()=>(await state()).settings.voiceInterruptions).toBe(false);
   await act('Open fallback options',page.getByText('If the preferred model is unavailable',{exact:true}));
   await evidence.action('Choose fallback model via native select',()=>page.getByLabel('Fallback model',{exact:true}).selectOption('gpt-live-1'));
   await expect(page.getByLabel('Fallback speaking voice',{exact:true})).toBeVisible();await evidence.action('Choose fallback voice via native select',()=>page.getByLabel('Fallback speaking voice',{exact:true}).selectOption('marin'));
   await expect.poll(async()=>(await state()).settings.fallbackVoiceName).toBe('marin');
   await expect(voicePage()).toContainText('Authentication errors and uncertain connection failures require your attention.');
   evidence.assertion('Changing fallback does not create sample',(await inspect()).samples.length,1);
   await act('Explicit fallback preview',page.getByRole('button',{name:'Preview fallback voice',exact:true}));await expect.poll(async()=>(await inspect()).samples.length).toBe(2);evidence.assertion('Fallback preview uses exact selected model/voice',(await inspect()).samples[1],{model:'gpt-live-1',voice:'marin'});
   await evidence.action('Disable fallback via native select',()=>page.getByLabel('Fallback model',{exact:true}).selectOption(''));await expect.poll(async()=>(await state()).settings.fallbackVoice).toBe('');await expect(page.getByRole('button',{name:'Preview fallback voice',exact:true})).toHaveCount(0);
   await evidence.action('Reload explicit fallback policy',()=>page.reload());await section('voice');await act('Reopen fallback choices',page.getByText('If the preferred model is unavailable',{exact:true}));await expect(page.getByLabel('Fallback model',{exact:true})).toHaveValue('');await expect(interruptions).not.toBeChecked();
  });
  await probe('unsupported-model',['voice.setup','voice.fallback'],async()=>{
   await evidence.action('Fixture prerequisite: saved retired model',()=>page.request.post(url+'/fixture/unsupported-model'));
   await expect(page.getByLabel('Voice model',{exact:true})).toHaveValue('retired-fixture-model');await expect(voicePage()).toContainText('This saved model is unavailable. Choose a supported model before your next call.');await expect(page.getByRole('button',{name:'Preview voice',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Choose a voice',exact:true})).toBeDisabled();
   evidence.assertion('No extra sample for unsupported model',(await inspect()).samples.length,2);
   evidence.assertion('No hidden conversation inputs',calls.filter(row=>['conversation.send','session.send'].includes(row.action)),[]);evidence.assertion('No external browser requests',external,[]);evidence.assertion('No browser errors',errors,[]);
  });
 }catch(error){failed=true;console.error(variant.id,error.message)}finally{
  releaseHeld?.();
  try{await browser?.close()}catch(error){failed=true;logs+='\nBrowser cleanup failure: '+error.message;console.error(variant.id,'browser cleanup',error.message)}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(output+'/'+variant.id+'-fixture.log',logs);await evidence.save()}
 }
}
await evidence.save();if(failed||evidence.probes.some(row=>row.status==='failed'))process.exitCode=1;
