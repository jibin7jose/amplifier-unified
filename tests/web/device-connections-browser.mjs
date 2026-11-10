// Runs only against the task-owned DTU service, with its synthetic account.
import {fixturePython} from './fixture-python.mjs';
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdtempSync,rmSync,mkdirSync} from 'node:fs';
import {spawn,execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {createServer} from 'node:http';

const base='/var/lib/device-connect',origin='https://127.0.0.1:8961';
assert.equal(execFileSync('hostname',{encoding:'utf8'}).trim(),'dtu-unified-device-connect-20261020');
const source=fileURLToPath(new URL('../../',import.meta.url));
const extension=join(source,'amplifier_web/device_client/extension');
const profile=mkdtempSync(base+'/browser/acceptance-');
const evidence=base+'/evidence';mkdirSync(evidence,{recursive:true});
const login=JSON.parse(readFileSync(base+'/access.json','utf8'));
const report={startedAt:new Date().toISOString(),origin,checks:[],platform:'Chromium on isolated Linux ARM64',nativePermissionPrompt:'Actual browser prompt accepted through isolated X11 input',tls:'Private CA installed in owned browser trust store; no ignore-certificate-errors flag'};
const check=(name,data={})=>{report.checks.push({name,...data});console.log('PASS:',name)};
let context,remoteBrowser,ref,owner,extensionID,fixtureServer;
const created=new Set();
async function signIn(page,path='/connect'){
 await page.goto(origin+path);
 if(page.url().includes('/login')){
  await page.locator('input[name=username]').fill(login.username);
  await page.locator('input[name=password]').fill(login.password);
  await page.locator('button[type=submit]').click();
 }
 await page.waitForURL(origin+path);
}
async function api(path,payload){
 return owner.evaluate(async({path,payload})=>{
  const prefix='/api/connections/v1';
  const headers={'Content-Type':'application/json'};
  if(payload!==undefined){const state=await(await fetch(prefix+'/devices')).json();headers['X-Connection-CSRF']=state.csrf}
  const response=await fetch(prefix+path,{method:payload===undefined?'GET':'POST',headers,...(payload===undefined?{}:{body:JSON.stringify(payload)})});
  const text=await response.text();
  return {status:response.status,body:response.headers.get('Content-Type')?.includes('json')?JSON.parse(text):text};
 },{path,payload});
}
async function until(fn,label,timeout=20000){
 const deadline=Date.now()+timeout;
 while(Date.now()<deadline){const value=await fn();if(value)return value;await new Promise(resolve=>setTimeout(resolve,250))}
 throw Error('Timed out: '+label);
}
async function popup(sample){
 await sample.bringToFront();
 const debug=await context.newCDPSession(sample);
 for(const old of (await debug.send('Target.getTargets')).targetInfos.filter(t=>t.url===`chrome-extension://${extensionID}/popup.html`)){
  await debug.send('Target.closeTarget',{targetId:old.targetId});
 }
 const windows=execFileSync('xdotool',['search','--onlyvisible','--class','chromium'],{encoding:'utf8'}).trim().split('\n');
 execFileSync('xdotool',['windowfocus','--sync',windows[0]]);
 await until(async()=>!(await debug.send('Target.getTargets')).targetInfos.some(t=>t.url===`chrome-extension://${extensionID}/popup.html`),'previous popup closed');
 execFileSync('xdotool',['key','--clearmodifiers','ctrl+shift+y']);
 const target=await until(async()=>(await debug.send('Target.getTargets')).targetInfos.find(t=>t.url===`chrome-extension://${extensionID}/popup.html`),'native toolbar popup');
 const {sessionId}=await debug.send('Target.attachToTarget',{targetId:target.targetId,flatten:false});
 let sequence=0;
 async function evaluate(expression){
  const id=++sequence;
  return new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{debug.off('Target.receivedMessageFromTarget',received);reject(Error('Popup evaluation timed out'))},5000);
   function received(event){
    if(event.sessionId!==sessionId)return;
    const message=JSON.parse(event.message);if(message.id!==id)return;
    clearTimeout(timer);debug.off('Target.receivedMessageFromTarget',received);
    if(message.error||message.result?.exceptionDetails)reject(Error(JSON.stringify(message.error||message.result.exceptionDetails)));
    else resolve(message.result?.result?.value);
   }
   debug.on('Target.receivedMessageFromTarget',received);
   debug.send('Target.sendMessageToTarget',{sessionId,message:JSON.stringify({id,method:'Runtime.evaluate',params:{expression,returnByValue:true}})}).catch(reject);
  });
 }
 await until(()=>evaluate('!!document.querySelector("#use")'),'popup controls');
 execFileSync('scrot',[evidence+'/browser-popup.png']);
 return {
  click:id=>evaluate(`document.querySelector(${JSON.stringify('#'+id)}).click()`),
  status:()=>evaluate('document.querySelector("#status").textContent'),
  close:async()=>{await debug.send('Target.closeTarget',{targetId:target.targetId});await debug.detach()},
 };
}
try{
 await until(()=>{try{return JSON.parse(execFileSync('curl',['--fail','--silent','--cacert',base+'/app/config/tls/ca.crt',origin+'/api/health'],{encoding:'utf8',stdio:['ignore','pipe','ignore']}))}catch{return false}},'owned service ready',30000);
 const launch=()=>chromium.launchPersistentContext(profile,{channel:'chromium',headless:false,viewport:{width:1280,height:1000},
  args:['--no-sandbox','--single-process','--no-zygote','--disable-dev-shm-usage',
   `--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
 context=await launch();
 let worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:15000});
 extensionID=worker.url().split('/')[2];assert.match(extensionID,/^[a-p]{32}$/);
 owner=await context.newPage();await signIn(owner);
 await owner.locator('#open-extension').waitFor();
 check('Actual PAM sign-in and rendered connection page over trusted HTTPS');
 const catalog=await owner.evaluate(async()=>await(await fetch('/connect/catalog')).json());
 assert.equal(catalog.extensionId,extensionID);
 const setupPagePromise=context.waitForEvent('page',{predicate:page=>page.url().includes('connect.html')});
 const consentPromise=context.waitForEvent('page',{timeout:90000,predicate:page=>page.url().includes('/connect/verify')});
 await owner.locator('#open-extension').click();
 await new Promise(resolve=>setTimeout(resolve,1000));
 execFileSync('scrot',[evidence+'/browser-native-prompt.png']);
 writeFileSync(evidence+'/browser-display.json',JSON.stringify({DISPLAY:process.env.DISPLAY,XAUTHORITY:process.env.XAUTHORITY}));
 // A fresh profile has no optional service-host grant. This accepts the
 // actual native prompt whose default focus is Deny, then Allow.
 execFileSync('xdotool',['key','--clearmodifiers','Tab','Return']);
 const setup=await setupPagePromise;await setup.waitForLoadState('domcontentloaded');
 assert.ok((await setup.locator('#host').textContent()).includes(origin));
 check('Web-to-installed-extension handoff carries host without typing');
 assert.ok(setup.url().includes('auto=1'),'This browser should support two-decision onboarding');
 const consent=await consentPromise;
 await consent.getByRole('button',{name:'Allow connection',exact:true}).waitFor();
 const code=await setup.locator('#code').textContent();
 assert.ok((await consent.locator('.code').textContent()).includes(code));
 await setup.locator('#qr-wrap:not([hidden])').waitFor();
 await until(()=>setup.locator('#qr').evaluate(img=>img.complete&&img.naturalWidth>0),'rendered approval QR');
 await setup.screenshot({path:evidence+'/connection-code.png'});
 await consent.getByRole('button',{name:'Allow connection',exact:true}).click();
 await setup.locator('#return:not([hidden])').waitFor({timeout:15000});
 const ownedDeviceId=await worker.evaluate(async()=>(await chrome.storage.local.get('connection')).connection.deviceID);
 const device=await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.id===ownedDeviceId&&d.connected)},'extension connected');
 created.add(device.id);report.extensionDeviceId=device.id;
 check('Proof-bound explicit approval, comparison code, QR and automatic readiness');
 report.onboarding={productDecisions:2,platformPermissionPrompts:1,typedHostAddresses:0,copiedTokens:0,installation:'Unpacked development extension preloaded by harness'};
 const sample=await context.newPage();await sample.goto(origin+'/connect/sample');
 let local=await popup(sample);
 await local.click('use');
 await until(()=>local.status().then(text=>text.includes('Selected:')),'local tab selection').catch(async error=>{
  console.error('Popup selection:',await local.status());
  console.error('Local selection state:',await worker.evaluate(async()=>{
   const c=(await chrome.storage.local.get('connection')).connection;
   return {stopped:c.stopped,target:c.target,error:c.error,lastSeen:c.lastSeen};
  }));throw error;
 });
 await local.close();
 const selected=await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.id===device.id)?.targets.find(t=>t.capabilities.includes('browser.observe'))},'registered exact document');
 check('Real toolbar gesture grants activeTab and registers exact browser document',{targetId:selected.id});
 remoteBrowser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox','--single-process','--no-zygote','--disable-dev-shm-usage']});
 const remote=await remoteBrowser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 owner=await remote.newPage();await signIn(owner);
 await until(()=>owner.locator('#target option').allTextContents().then(options=>options.some(text=>text.includes(selected.label))),'target in independent mobile client');
 check('Independent mobile-sized client discovers the desktop browser target');
 await owner.bringToFront();
 await owner.selectOption('#target',device.id+'/'+selected.id);
 const start=Date.now();
 await owner.getByRole('button',{name:'Fill and save the sample',exact:true}).click();
 await owner.getByText('Verified: Saved Ada blue.',{exact:true}).waitFor({timeout:60000});
 assert.equal(await sample.locator('#saved').textContent(),'Saved Ada blue.');
 assert.equal(await sample.locator('#count').textContent(),'1');
 const result=await api('/devices');
 const ops=result.body.operations.filter(r=>r.deviceId===device.id);
 assert.equal(ops.filter(r=>r.action==='browser.click'&&r.args.selector==='#save').length,1);
 assert.ok(ops.every(r=>r.status==='completed'));
 check('Remote request fills and saves actual selected document exactly once',{seconds:(Date.now()-start)/1000,operations:ops.length,modelCalls:0});
 await owner.screenshot({path:evidence+'/connection-completed.png',fullPage:true});
 assert.ok(await owner.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile page must not overflow horizontally');
 local=await popup(sample);await local.click('stop');await until(()=>local.status().then(text=>!text.includes('Selected:')),'local stop');await local.close();
 await until(async()=>{const r=await api('/devices');return !r.body.devices.find(d=>d.id===device.id)?.targets.some(t=>t.id===selected.id)},'local stop reflected');
 const blocked=await api('/operations',{id:crypto.randomUUID(),deviceId:device.id,targetId:selected.id,revision:selected.revision,action:'browser.click',args:{selector:'#save'}});
 assert.equal(blocked.status,409);assert.equal(await sample.locator('#count').textContent(),'1');
 check('Local Stop prevents further actions without revoking the app');
 local=await popup(sample);await local.click('use');await until(()=>local.status().then(text=>text.includes('Selected:')),'reselection');await local.close();
 const again=await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.id===device.id)?.targets.find(t=>t.capabilities.includes('browser.observe'))},'reselected browser document');
 await sample.reload();
 await until(async()=>{const r=await api('/devices');return !r.body.devices.find(d=>d.id===device.id)?.targets.some(t=>t.id===again.id)},'navigation invalidates target',20000);
 assert.equal((await api('/operations',{id:crypto.randomUUID(),deviceId:device.id,targetId:again.id,revision:again.revision,action:'browser.click',args:{selector:'#save'}})).status,409);
 check('Navigation does not transfer access to a new document');
 fixtureServer=createServer((request,response)=>{
  response.writeHead(200,{'Content-Type':'text/html'});
  response.end('<!doctype html><title>Connection sample outside service</title><h1>Independent browser target</h1><button id="increment">Add one</button><p id="counter">0</p><script>document.querySelector("#increment").onclick=()=>{const n=document.querySelector("#counter");n.textContent=String(Number(n.textContent)+1)}</script>');
 });
 await new Promise(resolve=>fixtureServer.listen(0,'127.0.0.1',resolve));
 const independentOrigin='http://localhost:'+fixtureServer.address().port;
 assert.equal(await worker.evaluate(async pattern=>chrome.permissions.contains({origins:[pattern]}),independentOrigin+'/*'),false);
 const independent=await context.newPage();await independent.goto(independentOrigin);
 local=await popup(independent);await local.click('use');await until(()=>local.status().then(text=>text.includes('Selected:')),'independent site selection');await local.close();
 const external=await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.id===device.id)?.targets.find(t=>t.label==='Connection sample outside service')},'independent browser target');
 const externalId=crypto.randomUUID();
 await api('/operations',{id:externalId,deviceId:device.id,targetId:external.id,revision:external.revision,action:'browser.click',args:{selector:'#increment'}});
 await until(async()=>{const r=await api('/operations/'+externalId);return r.body.status==='completed'},'independent page action');
 assert.equal(await independent.locator('#counter').textContent(),'1');
 check('activeTab authorizes a real separate-origin page without a persistent site grant');
 // New full-tab grant is explicitly approved; existing document access is unchanged.
 await setup.bringToFront();
 const upgradeConsent=context.waitForEvent('page',{predicate:p=>p.url().includes('/connect/verify')});
 await setup.locator('#task-upgrade').click();
 const upgrade=await upgradeConsent;await upgrade.getByRole('button',{name:'Allow added access',exact:true}).click();
 await until(()=>worker.evaluate(async()=>(await chrome.storage.local.get('connection')).connection.grants.includes('browser.task')),'explicit full-tab grant');
 await setup.locator('#task-upgrade').waitFor({state:'hidden',timeout:15000});
 local=await popup(independent);await local.click('use-task');await until(()=>local.status().then(text=>text.includes('Selected:')),'full-tab local selection');await local.close();
 const taskTarget=await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.id===device.id)?.targets.find(t=>t.capabilities.includes('browser.task'))},'full-tab ready');
 async function taskOp(action,args={}){
  const id=crypto.randomUUID();const submitted=await api('/operations',{id,deviceId:device.id,targetId:taskTarget.id,revision:taskTarget.revision,action,args});assert.equal(submitted.status,200);
  const receipt=await until(async()=>{const r=await api('/operations/'+id);return !['queued','delivered'].includes(r.body.status)?r.body:null},action,20000);
  assert.equal(receipt.status,'completed',JSON.stringify(receipt));return receipt.result;
 }
 let observed=await taskOp('browser.task.observe');const increment=observed.state.elements.find(e=>e.name==='Add one');assert.ok(increment);
 const firstAction=await taskOp('browser.task.act',{revision:observed.revision,candidate:increment.id+':click',text:''});assert.equal(firstAction.status,'completed',JSON.stringify(firstAction));
 assert.equal(await independent.locator('#counter').textContent(),'2');
 assert.equal((await taskOp('browser.task.open',{url:independentOrigin+'/next'})).status,'completed');
 observed=await taskOp('browser.task.observe');assert.equal(new URL(observed.state.url).pathname,'/next');
 assert.equal((await taskOp('browser.task.act',{revision:observed.revision,candidate:observed.state.elements.find(e=>e.name==='Add one').id+':click',text:''})).status,'completed');
 assert.equal(await independent.locator('#counter').textContent(),'1');
 const shot=await taskOp('browser.task.screenshot');assert.ok(shot.image.length>1000);assert.equal(shot.mimeType,'image/jpeg');
 check('Explicit full-tab upgrade supports observation, navigation, fresh actions and live screenshot');
 // The live-model harness is supplied by the owned qualification environment.
 // Protocol acceptance alone must not be reported as a successful model chat.
 if(process.env.UI_USE_LIVE_CASE){
 const chat=spawn(fixturePython(process.env.UI_USE_TEST_PYTHON||join(source,'.venv/bin/python')),[process.env.UI_USE_LIVE_CASE,'--name','connected-browser-chat',
  '--target',device.id+':'+taskTarget.id,'--expect-text','Navigation verified',
  '--prompt','Use the connected browser selected for this chat. Open http://127.0.0.1:8962/ . Set Name to Ada, choose Team Blue, save and verify Saved Ada blue. Then click Continue and verify Navigation verified. Use UI use with its configured decision selection.'],{stdio:['ignore','pipe','pipe']});
 let chatLog='';chat.stdout.on('data',chunk=>chatLog+=chunk);chat.stderr.on('data',chunk=>chatLog+=chunk);
 const chatCode=await new Promise(resolve=>chat.on('exit',resolve));writeFileSync(evidence+'/connected-browser-chat.log',chatLog);
 assert.equal(chatCode,0,chatLog);assert.equal(new URL(independent.url()).pathname,'/next');
 assert.match(await independent.locator('body').innerText(),/Navigation verified/);
 check('Normal chat uses configured decision model through connected tab across pages with independent page verification');
 }else{
  report.liveChat={skipped:true,reason:'UI_USE_LIVE_CASE was not supplied'};
  console.log('SKIP: optional live-model chat harness; protocol checks continue');
 }
 local=await popup(independent);await local.click('stop');await local.close();
 await until(()=>worker.evaluate(()=>attachedTab===null),'debugger detached on local stop');
 assert.equal((await api('/operations',{id:crypto.randomUUID(),deviceId:device.id,targetId:taskTarget.id,revision:taskTarget.revision,action:'browser.task.observe',args:{}})).status,409);
 check('Full-tab local Stop detaches debugger and removes readiness');
 await setup.bringToFront();await setup.locator('#advanced summary').click();await setup.locator('#rotate').click();
 await until(()=>worker.evaluate(async()=>!(await chrome.storage.local.get('connection')).connection.rotationId),'credential rotation',20000);
 assert.equal((await api('/devices')).body.devices.filter(d=>d.id===device.id).length,1);
 check('Credential rotation preserves enrollment');
 const lastSeen=(await api('/devices')).body.devices.find(d=>d.id===device.id).lastSeen;
 await context.close();context=await launch();
 worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:15000});
 await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.id===device.id)?.lastSeen>lastSeen},'browser reconnect without pairing',20000);
 check('Browser restart reconnects with stored enrollment and no new approval');
 // A separately implemented process uses no host imports or browser extension.
 const referenceState=join(profile,'reference.json');
 ref=spawn(fixturePython(join(source,'.venv/bin/python')),[join(source,'amplifier_web/device_client/reference_client.py'),'--host',origin,
  '--ca',base+'/app/config/tls/ca.crt','--state',referenceState,'--no-browser','--name','Independent acceptance client'],
  {stdio:['ignore','pipe','pipe']});
 let output='',errors='';ref.stdout.on('data',chunk=>output+=chunk);ref.stderr.on('data',chunk=>errors+=chunk);
 const pending=await until(()=>output.split('\n').flatMap(line=>{try{return[JSON.parse(line)]}catch{return[]}}).find(r=>r.status==='approval_required'),'reference client approval');
 const refConsent=await remote.newPage();await refConsent.goto(pending.url);
 assert.ok((await refConsent.locator('.code').textContent()).includes(pending.code));
 await refConsent.getByRole('button',{name:'Allow connection',exact:true}).click();
 const native=await until(async()=>{const r=await api('/devices');return r.body.devices.find(d=>d.appId==='reference-python-v1'&&d.connected)},'reference connected');
 created.add(native.id);
 const ping=native.targets[0],id=crypto.randomUUID();
 assert.equal((await api('/operations',{id,deviceId:native.id,targetId:ping.id,revision:ping.revision,action:'connection.ping',args:{}})).status,200);
 const receipt=await until(async()=>{const r=await api('/operations/'+id);return r.body.status==='completed'?r.body:null},'reference receipt');
 assert.match(receipt.result.message,/independent Python/);
 check('Independent Python client consumes public contract and completes delivery');
 await api('/devices/'+native.id+'/revoke',{});
 await until(()=>ref.exitCode!==null,'reference exits after revocation',20000);
 assert.equal(ref.exitCode,1);
 check('Revocation terminates independent client access without automatic re-enrollment');
 await api('/devices/'+device.id+'/revoke',{});
 await until(()=>worker.evaluate(async()=>(await chrome.storage.local.get('connection')).connection.revoked),'extension revocation',20000);
 check('Revocation observed by extension and retained as explicit reconnect requirement');
 report.passed=true;
}catch(error){
 report.passed=false;report.error=error.stack;
 report.pages=await Promise.all((context?.pages()||[]).map(async page=>({url:page.url(),text:await page.locator('body').innerText().catch(()=>'<unavailable>')})));
 console.error(error);
 console.error(JSON.stringify(report.pages));
 if(owner)await owner.screenshot({path:evidence+'/connection-failure.png',fullPage:true}).catch(()=>{});
 process.exitCode=1;
}finally{
 if(owner)for(const id of created)await api('/devices/'+id+'/revoke',{}).catch(()=>{});
 ref?.kill('SIGTERM');
 await context?.close();
 await remoteBrowser?.close();
 if(fixtureServer)await new Promise(resolve=>fixtureServer.close(resolve));
 rmSync(profile,{recursive:true,force:true});
 report.completedAt=new Date().toISOString();writeFileSync(evidence+'/device-connections-browser.json',JSON.stringify(report,null,2)+'\n');
}
