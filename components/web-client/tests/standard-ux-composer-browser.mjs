import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real ordinary controls/host, private synthetic state. Speech events are
// simulated; this does not qualify microphone hardware or provider inference.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';
import {openSettingsPage} from './browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('An owned evidence directory is required');
await mkdir(output,{recursive:true});
const evidence=new StandardUxEvidence(output,{baseline:'9fc794879',sourceReceipt:'execution-source.json',data:'synthetic-standard',runtime:'Production host and built client; synthetic recording runtime and browser speech events',timings:'Shared-load functional observations only'});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',release,failed=false;
fixture.stderr.on('data',chunk=>logs+=chunk);
const bounded=async(promise,label)=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),8000)})])}finally{clearTimeout(timer)}};
const unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 page=await browser.newPage({viewport:{width:1366,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await page.addInitScript(()=>{window.speechCalls=0;window.SpeechRecognition=class{constructor(){window.speech=this}start(){window.speechCalls++}stop(){this.onend?.()}abort(){this.aborted=true}}});
 page.setDefaultTimeout(7000);evidence.page=page;
 const errors=[],calls=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(unwrap(request.postDataJSON()))});
 const state=()=>page.evaluate(()=>window.amplifier.getState()),inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const input=page.getByRole('textbox',{name:'Message Amplifier',exact:true}),files=page.getByLabel('Attach files',{exact:true}),strip=page.locator('.a-composer .a-attachments');
 const click=(label,locator)=>evidence.action(label,()=>locator.click());
 const probe=async(id,workflows,run)=>{const row=await evidence.probe(id,workflows,async()=>{await run();return {variant:id.startsWith('narrow')?'narrow-pointer':'desktop-pointer',...await evidence.snapshot(page,id,'#amp-one')}});if(row.status!=='passed')throw Error('Dependent checks stopped after '+id)};
 let original;
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await expect(input).toBeEditable();
 await probe('narrow-options-dismiss-focus',['composer.options'],async()=>{
  await input.fill('Keep this unsent draft');await page.setViewportSize({width:390,height:844});
  const trigger=page.getByRole('button',{name:'Model and reasoning settings',exact:true}),menu=page.getByRole('dialog',{name:'Chat options',exact:true});
  await click('Open ordinary narrow options',trigger);await expect(menu.getByRole('button',{name:'Choose model',exact:true})).toBeFocused();
  const before=(await state()).view.newSessionDraft??null;
  await menu.getByRole('button',{name:'Choose model',exact:true}).press('Escape');await expect(menu).toHaveCount(0);await expect(trigger).toBeFocused();
  evidence.assertion('Escape keeps exact selection',(await state()).view.newSessionDraft??null,before);await expect(input).toHaveDraft('Keep this unsent draft');
  await click('Open options again',trigger);await click('Close options without selecting',menu.getByRole('button',{name:'Close chat options',exact:true}));await expect(trigger).toBeFocused();
  evidence.assertion('Close options never sends',(await inspect()).sent.length,0);await page.setViewportSize({width:1366,height:1000});
 });
 await probe('desktop-upload-pending-and-duplicate',['composer.attachment-add','composer.send'],async()=>{
  await input.fill('Create the attachment owner');await click('Create one intentional chat',page.getByRole('button',{name:'Send message',exact:true}));await expect(page.locator('.a-assistant')).toContainText('Synthetic first response');original=(await state()).selectedSessionId;
  await input.fill('Private upload draft');let entered;const held=new Promise(resolve=>entered=resolve);let first=true;
  await page.route('**/api/actions',async route=>{const body=unwrap(route.request().postDataJSON());if(body?.action==='attachment.add'&&first){first=false;await new Promise(resolve=>{release=resolve;entered()})}await route.continue()});
  try{await evidence.action('Choose two exact text files',()=>files.setInputFiles([{name:'one.txt',mimeType:'text/plain',buffer:Buffer.from('One original byte sequence')},{name:'two.txt',mimeType:'text/plain',buffer:Buffer.from('Two original byte sequence')}]));await bounded(held,'Held upload request');
   await expect(page.getByText('Uploading attachments…',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeDisabled();await input.press('Control+Enter');evidence.assertion('Pending upload sends nothing',(await inspect()).sent.length,1);await expect(input).toHaveDraft('Private upload draft');
   release();release=null;await expect(strip.locator('.a-attachment')).toHaveCount(2);await expect(page.getByText('Uploading attachments…',{exact:true})).toHaveCount(0);
  }finally{release?.();release=null;await page.unrouteAll({behavior:'wait'})}
  await evidence.action('Select same file explicitly again',()=>files.setInputFiles({name:'one.txt',mimeType:'text/plain',buffer:Buffer.from('One original byte sequence')}));await expect(strip.locator('.a-attachment')).toHaveCount(3);
  const uploaded=await strip.locator('.a-attachment a[title]').evaluateAll(rows=>rows.map(row=>({name:row.title,url:row.getAttribute('href'),label:row.textContent})));
  evidence.assertion('Duplicate selection has three distinct attachment identities',new Set(uploaded.map(row=>row.url)).size,3);evidence.assertion('Selected file names are exact',uploaded.map(row=>row.name),['one.txt','two.txt','one.txt']);
  evidence.assertion('Size shown for every chosen file',uploaded.every(row=>row.label.includes('1 KB')));await click('Remove only explicit duplicate',strip.getByRole('button',{name:'Remove one.txt',exact:true}).last());await expect(strip.locator('.a-attachment')).toHaveCount(2);
 });
 await probe('desktop-late-upload-owner-isolation',['composer.attachment-add','composer.attachment-use'],async()=>{
  let entered;const held=new Promise(resolve=>entered=resolve);await page.route('**/api/actions',async route=>{const body=unwrap(route.request().postDataJSON());if(body?.action==='attachment.add')await new Promise(resolve=>{release=resolve;entered()});await route.continue()});
  try{await files.setInputFiles({name:'late.txt',mimeType:'text/plain',buffer:Buffer.from('Only the old chat owns this')});await bounded(held,'Held upload request');await click('Open navigation while upload is pending',page.getByRole('button',{name:'Open navigation',exact:true}));await click('Navigate to a different draft while upload is pending',page.getByRole('button',{name:'New chat',exact:true}));await expect.poll(async()=>(await state()).selectedSessionId).toBe(null);release();release=null;await expect(page.getByText('Uploading attachments…',{exact:true})).toHaveCount(0);await expect(strip).toHaveCount(0);await expect(input).toHaveDraft('');}
  finally{release?.();release=null;await page.unrouteAll({behavior:'wait'})}
  if(await page.getByRole('button',{name:'Open navigation',exact:true}).isVisible())await click('Open navigation',page.getByRole('button',{name:'Open navigation',exact:true}));await click('Return to exact upload owner',page.getByRole('region',{name:'Recent chats',exact:true}).getByRole('button',{name:'Create the attachment owner',exact:true}));await expect.poll(async()=>(await state()).selectedSessionId).toBe(original);await expect(strip.locator('.a-attachment')).toHaveCount(3);await expect(input).toHaveDraft('Private upload draft');
  await click('Remove only late draft attachment',strip.getByRole('button',{name:'Remove late.txt',exact:true}));await expect(strip.locator('.a-attachment')).toHaveCount(2);evidence.assertion('Navigation and upload never sent more inputs',(await inspect()).sent.length,1);
 });
 await probe('desktop-attachment-send-preview-and-remove',['composer.attachment-use','composer.send'],async()=>{
  await click('Send draft with exactly two attachments',page.getByRole('button',{name:'Send message',exact:true}));await expect.poll(async()=>(await inspect()).sent.length).toBe(2);const delivered=(await inspect()).sent[1];evidence.assertion('Sent attachment owner is exact',delivered.sessionId,original);evidence.assertion('Sent attachment names are exact',delivered.attachments.map(row=>row.name),['one.txt','two.txt']);
  const message=page.locator('.a-user').filter({hasText:'Private upload draft'});await expect(message.locator('.a-attachment')).toHaveCount(2);await click('Open sent text file in Canvas',message.getByRole('button',{name:/^one.txt/}));await expect.poll(async()=>(await state()).canvas?.content).toBe('One original byte sequence');evidence.assertion('Canvas belongs to attachment chat',(await state()).canvas.sessionId,original);
  await click('Close attachment preview',page.getByRole('button',{name:'Close canvas',exact:true}));await files.setInputFiles({name:'one.txt',mimeType:'text/plain',buffer:Buffer.from('New unsent bytes')});await expect(strip.locator('.a-attachment')).toHaveCount(1);await click('Remove new draft copy only',strip.getByRole('button',{name:'Remove one.txt',exact:true}));await expect(strip).toHaveCount(0);await expect(message.locator('.a-attachment')).toHaveCount(2);
  const response=await page.request.get(url+delivered.attachments[0].url);evidence.assertion('Removal leaves sent bytes unchanged',await response.text(),'One original byte sequence');
 });
 await probe('desktop-upload-error-keeps-draft',['composer.attachment-error'],async()=>{
  await input.fill('Never lose this failure draft');const before=calls.filter(row=>row.action==='attachment.add').length;
  await files.setInputFiles([{name:'before-failure.txt',mimeType:'text/plain',buffer:Buffer.from('Retain this successful attachment')},{name:'empty-evidence.txt',mimeType:'text/plain',buffer:Buffer.alloc(0)},{name:'not-attempted.txt',mimeType:'text/plain',buffer:Buffer.from('Do not upload after failure')}]);
  await expect(page.getByText(/Choose a nonempty file up to 32 MB/)).toBeVisible();await expect(input).toHaveDraft('Never lose this failure draft');await expect(strip.locator('.a-attachment')).toHaveCount(1);await expect(strip.getByRole('link',{name:'before-failure.txt 1 KB',exact:true})).toBeVisible();
  evidence.assertion('Failed intake sends nothing',(await inspect()).sent.length,2);evidence.assertion('Only successful preceding file reached the server',calls.filter(row=>row.action==='attachment.add').length-before,1);
  const alert=page.getByRole('alert');await expect(alert).toContainText('empty-evidence.txt: Choose a nonempty file up to 32 MB.');await expect(alert).toContainText('1 remaining file was not uploaded.');
  await click('Dismiss known file failure',alert.getByRole('button',{name:'Dismiss error',exact:true}));await click('Remove successful draft file explicitly',strip.getByRole('button',{name:'Remove before-failure.txt',exact:true}));await expect(strip).toHaveCount(0);

 });
 await probe('desktop-dictation-denial-and-explicit-text',['composer.dictate'],async()=>{
  await input.fill('Retain this dictation draft');evidence.assertion('Speech did not start automatically',await page.evaluate(()=>speechCalls),0);await click('Explicitly start dictation',page.getByRole('button',{name:'Dictate message',exact:true}));await expect(page.getByText('Listening…',{exact:true})).toBeVisible();await page.evaluate(()=>speech.onerror({error:'not-allowed'}));await expect(page.getByText('Allow microphone access in your browser to dictate.',{exact:true})).toBeVisible();await expect(input).toHaveDraft('Retain this dictation draft');
  await click('Explicitly retry browser dictation',page.getByRole('button',{name:'Dictate message',exact:true}));await page.evaluate(()=>speech.onresult({resultIndex:0,results:[Object.assign([{transcript:'spoken addition'}],{isFinal:true})]}));await expect(input).toHaveDraft('Retain this dictation draft spoken addition');await click('Stop dictation without sending',page.getByRole('button',{name:'Stop dictation',exact:true}));evidence.assertion('Speech never submits text or starts a call',calls.filter(row=>['conversation.send','call.start'].includes(row.action)).length,2);evidence.assertion('Speech recognition configured actual browser locale',await page.evaluate(()=>speech.lang),await page.evaluate(()=>navigator.language));
 });
 await probe('desktop-dictation-unsupported-guidance',['composer.dictate'],async()=>{
  await page.addInitScript(()=>{delete window.SpeechRecognition;delete window.webkitSpeechRecognition});await page.reload();await expect(input).toHaveDraft('Retain this dictation draft spoken addition');await expect(page.getByRole('button',{name:'Dictate message',exact:true})).toHaveCount(0);await openSettingsPage(page,'voice');await expect(page.getByText('This browser does not support speech recognition. You can use keyboard dictation where available.',{exact:true})).toBeVisible();evidence.assertion('Unsupported device never sends or starts',(await inspect()).sent.length,2);evidence.assertion('No browser exceptions',errors,[]);
 });
}catch(error){
 failed=true;console.error(error.stack||error);
 if(!evidence.probes.some(row=>row.status==='failed'))evidence.probes.push({id:browser?'runner':'browser-startup',status:'failed',error:error.message,coverage:'no-workflow-claim'});
}finally{
 release?.();await writeFile(output+'/fixture.log',logs);await evidence.save();await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){const ended=once(fixture,'exit');fixture.kill('SIGTERM');let timer;try{await Promise.race([ended,new Promise(resolve=>{timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},10000)})])}finally{clearTimeout(timer)}}
 if(failed)process.exitCode=1;
}
