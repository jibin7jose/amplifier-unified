import {createShellHttpFixture} from './shell-http-fixture.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
// Source renderer; synthetic Host/SSE/catalog only. Never forwards runtime/model work.
import {readComposerDraft} from './composer-test-helpers.mjs';
// Run --probe first in the manager's DTU. Each invocation has one context/page and a 60s budget.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const sid='caption-chat',workspace='/fixture',provider={id:'fixture-provider',
 info:{display_name:'Test',defaults:{model:'model'},config_fields:[]},configSchema:{fields:[]}};
const catalog={configurationRevision:0,providers:[provider],effective:{instance:provider.id,model:'model'}};
let state={revision:1,client:{hostInstanceId:'caption-fixture'},settings:{workspace},runtime:{available:true},
 voice:{status:'disconnected'},view:{mode:'chat',workSurface:'chat',navPinned:false,draft:''},
 sessions:[{id:sid,title:'Caption fixture',sessionKind:'root',workspace,workspaceId:'project',
  location:{kind:'workspace',path:workspace},bundle:'work',status:'idle',historyManaged:true,historyLoaded:true,
  messages:[{id:'saved',role:'assistant',text:'Saved fixture response'}],workers:[],questions:[],approvals:[]}],
 workspaces:[{id:'project',name:'Fixture',path:workspace,available:true}],selectedSessionId:sid,selectedWorkspaceId:'project',
 setup:{providers:[provider],providersLoadedAt:1,providersWorkspace:workspace},
 registeredBundles:[{value:'work',label:'Work'}],runtimeControl:{[sid]:{'configuration.catalog':catalog}},canvas:{open:false}};
const shell=createShellHttpFixture(()=>state);
const calls=[],blocked=[],errors=[],held=[],proof={scope:'Source renderer; synthetic Host/SSE/catalog, no runtime or inference',
 contextCount:0,pageCount:0,widths:[]};
const proofDir=process.env.COMPOSER_CAPTION_PROOF_DIR;
let vite,browser,context,page,origin,timer;
const persist=async name=>{if(proofDir){await mkdir(proofDir,{recursive:true});await writeFile(proofDir+'/'+name+'.json',JSON.stringify(proof,null,2))}};
const snap=async name=>{if(proofDir){await page.screenshot({path:proofDir+'/'+name+'.png'});await writeFile(proofDir+'/'+name+'-state.json',JSON.stringify(await page.evaluate(()=>window.amplifier.getState()),null,2))}};
const emit=async()=>{state.revision++;await page.evaluate(value=>window.emitState(value),shell.state())};
const composer=()=>page.getByRole('textbox',{name:'Message Amplifier',exact:true});
const status=()=>page.locator('.a-composer .a-sr-only[role="status"]');
const primary=()=>page.locator('[data-part="composer-primary-action"]');
const sendCount=()=>calls.filter(row=>row.action==='conversation.send').length;
const geometry=async()=>{
 const elements={surface:page.locator('[data-part="composer-surface"]'),controls:page.locator('.a-compose-configuration'),primary:primary(),
  attachment:page.getByRole('button',{name:'Add attachments',exact:true}),
  providerModel:page.getByRole('button',{name:'Model and reasoning settings',exact:true}),
  providerModelLabel:page.getByRole('button',{name:'Model and reasoning settings',exact:true}).locator('span')};
 const result={};
 for(const [name,element] of Object.entries(elements)){
  await expect(element).toBeVisible();const box=await element.boundingBox();
  assert.ok(box&&box.width>0&&box.height>0,'Missing '+name+' rectangle');
  result[name]=box;
 }
 return result;
};
const aligned=(idle,actual,phase)=>{
 for(const name of Object.keys(idle))for(const axis of ['x','y','width','height'])
  assert.ok(Math.abs(actual[name][axis]-idle[name][axis])<=1,
   `${phase}: ${name}.${axis} shifted ${actual[name][axis]-idle[name][axis]} CSS px`);
};
async function accessibleSending(){
 await expect(status()).toHaveText('Sending message…');
 await expect(status()).toHaveAttribute('aria-live','polite');
 await expect(status()).toHaveAttribute('aria-atomic','true');
 // Playwright "visible" alone cannot distinguish clipped text from painted text.
 const css=await status().evaluate(el=>{
  const value=getComputedStyle(el),box=el.getBoundingClientRect();
  return {display:value.display,visibility:value.visibility,position:value.position,clip:value.clip,
   clipPath:value.clipPath,width:box.width,height:box.height,hidden:!!el.closest('[hidden],[aria-hidden="true"],[inert]')};
 });
 assert.notEqual(css.display,'none');assert.notEqual(css.visibility,'hidden');assert.equal(css.hidden,false);
 assert.equal(css.position,'absolute');assert.equal(css.clipPath,'inset(50%)');assert.equal(css.clip,'rect(0px, 0px, 0px, 0px)');
 assert.ok(css.width<=1&&css.height<=1,'No painted normal caption box');
 const ax=await status().ariaSnapshot();assert.match(ax,/status/);assert.match(ax,/Sending message…/);
 await expect(page.locator('.a-compose-configuration [role="status"]')).toHaveCount(0);
 assert.equal(await page.getByText('Sending message…',{exact:true}).count(),1,'Only one composer sending announcement');
 return {css,accessibility:ax};
}
async function nextSend(){
 await expect.poll(()=>held.length,{timeout:8000}).toBe(1);
 const item=held.shift();assert.equal(item.body.action,'conversation.send');assert.equal(item.body.args.sessionId,sid);
 return item;
}
async function run(){
 // Dependency/import or browser-launch failures are prerequisites, not product acceptance.
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();origin=new URL(vite.resolvedUrls.local[0]).origin;
 browser=await chromium.launch({headless:true,timeout:15000,
  ...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{}),
  args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 context=await browser.newContext({viewport:{width:1280,height:900},hasTouch:true,serviceWorkers:'block'});proof.contextCount++;
 page=await context.newPage();proof.pageCount++;page.setDefaultTimeout(8000);
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(initial=>{
  const sources=[];window.captionBootstrap={baselineCount:0,sourceCount:0};
  window.EventSource=class extends EventTarget{
   constructor(url){super();this.url=url;this.readyState=1;sources.push(this);window.captionBootstrap.sourceCount++;
    // The app installs its listeners in the constructor's stack, before this event.
    queueMicrotask(async()=>{const current=await fetch('/api/state').then(response=>response.json());window.captionBootstrap.baselineCount++;this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(current)}));this.onopen?.()});
   }
   close(){this.readyState=2}
  };
  window.emitState=value=>sources.forEach(source=>source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(value)})));
 },state);
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin){blocked.push(request.url());return route.abort()}
  if(!url.pathname.startsWith('/api/'))return route.continue();
  const shellResponse=shell.response(request);
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(url.pathname==='/api/state')return route.fulfill({json:shell.state()});
  if(url.pathname==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
  if(url.pathname==='/api/view')return route.fulfill({json:{accepted:true}});
  if(url.pathname!=='/api/actions'){blocked.push(url.pathname);return route.abort()}
  const envelope=request.postDataJSON(),body=envelope.action==='shell.command'?(envelope.args.command||envelope.args):envelope;calls.push(body);
  if(body.action==='conversation.send'){held.push({route,body});return}
  if(body.action==='conversation.delivery')return route.fulfill({json:{accepted:true,result:{
   delivery:'not_saved',message:'Fixture: no saved copy. Nothing was resent.'},state:shell.state()}});
  if(body.action==='view.update'){state.view={...state.view,...body.args.patch};state.revision++}
  else if(!['shell.report','attention.read','bundles.list','configuration.defaults','providers.list'].includes(body.action)&&
   !(body.action==='runtime.control'&&body.args.operation==='configuration.catalog')){
   blocked.push(body.action);return route.abort();
  }
  return route.fulfill({json:{accepted:true,operationId:body.id,state:shell.state()}});
 });
 await page.goto(origin,{waitUntil:'domcontentloaded',timeout:15000});
 await expect(composer()).toBeEditable();
 await expect(page.getByRole('button',{name:'Model and reasoning settings',exact:true})).toHaveText('model');
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await showComposerOption(page,'bundle');
 await expect(page.getByLabel('Registered root bundles',{exact:true}).locator('option:checked')).toHaveText('Work');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 proof.readiness=await page.evaluate(()=>({stream:window.captionBootstrap,selectedSessionId:window.amplifier.getState().selectedSessionId,
  shell:window.amplifier.getShellState()}));
 assert.equal(proof.readiness.selectedSessionId,sid);assert.equal(proof.readiness.stream.baselineCount,1);
 assert.equal(proof.readiness.stream.sourceCount,1);assert.ok(proof.readiness.shell);
 assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.equal(sendCount(),0);
 proof.status='READY';await persist('readiness');await snap('ready');
 if(process.argv.includes('--probe'))return;
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  const text='Held send '+width,nextDraft='Next draft '+width;
  const rich='**Kept bold '+width+'** and `code`';
  await composer().press('ControlOrMeta+a');await composer().press('Backspace');
  await composer().evaluate((node,text)=>{const clipboardData=new DataTransfer();clipboardData.setData('text/plain',text);node.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))},rich);
  await expect(composer()).toHaveDraft(rich);
  await expect(composer().locator('strong')).toHaveText('Kept bold '+width);
  await expect(composer().locator('code')).toHaveText('code');
  // Replacing rich text with fill() legitimately inherits its active marks.
  // Paste the intended plain Markdown so this geometry fixture sends plain text.
  await composer().press('ControlOrMeta+a');
  await composer().evaluate((node,text)=>{const clipboardData=new DataTransfer();clipboardData.setData('text/plain',text);node.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))},text);
  await expect(composer()).toHaveDraft(text);await expect(composer().locator('strong,code')).toHaveCount(0);
  await expect(primary()).toHaveAttribute('data-action','conversation.send');await expect(primary()).toBeEnabled();
  await expect(status()).toBeEmpty();
  const row={width,idle:await geometry()};proof.widths.push(row);
  const before=sendCount();
  if(width===1280)await composer().press('Enter');else await primary().tap();
  const item=await nextSend();assert.equal(item.body.args.text,text);assert.equal(sendCount()-before,1);
  row.announcement=await accessibleSending();row.sending=await geometry();aligned(row.idle,row.sending,'sending '+width);
  await expect(page.locator('.a-user').filter({hasText:text}).getByRole('status')).toContainText('Sending…');
  await expect(composer()).toHaveDraft('');await expect(composer()).toBeEditable();
  // Trusted keyboard typing and toolbar traversal while the delivery is held.
  await composer().focus();await page.keyboard.type(nextDraft);
  await expect(composer()).toHaveDraft(nextDraft);await expect(composer()).toContainText(nextDraft);
  await page.getByRole('button',{name:'Add attachments',exact:true}).focus();
  await page.keyboard.press('Tab');await expect(primary()).toBeFocused();
  await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'Model and reasoning settings',exact:true})).toBeFocused();
  // Current main deliberately moves Chat controls into the header Chat menu.
  await page.getByRole('button',{name:'Chat',exact:true}).focus();
  await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeFocused();
  await expect(page.getByRole('button',{name:'Chat controls',exact:true})).toHaveCount(0);
  row.keyboard=await geometry();aligned(row.idle,row.keyboard,'keyboard '+width);await snap(width+'-sending');
  state.sessions[0].status='working';await emit();
  await expect(page.locator('.a-live-activity')).toBeVisible();
  row.working=await geometry();aligned(row.idle,row.working,'working '+width);await snap(width+'-working');
  state.sessions[0].status='idle';await emit();await expect(page.locator('.a-live-activity')).toHaveCount(0);
  const failure='Fixture send rejection '+width;
  await item.route.fulfill({status:409,json:{accepted:false,error:failure}});
  const bubble=page.locator('.a-user').filter({hasText:text});
  await expect(bubble.getByRole('alert')).toHaveText(failure);await expect(bubble.getByRole('alert')).toBeVisible();
  await expect(page.getByText(failure,{exact:true}),'No duplicated composer error').toHaveCount(1);
  await expect(bubble.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
  await expect(status()).toBeEmpty();await expect(composer()).toHaveDraft(nextDraft);await expect(composer()).toContainText(nextDraft);await expect(composer()).toBeEditable();
  assert.equal(sendCount()-before,1,'Typing/state/rejection must not submit another message');
  row.rejected={errorVisible:true,retryVisible:true,nextDraft:await readComposerDraft(composer()),sendPosts:sendCount()-before};
  await snap(width+'-rejected');
  await bubble.getByRole('button',{name:'Discard unsent message',exact:true}).click();await expect(bubble).toHaveCount(0);
  // Unknown local submission retains Check delivery and the original-identity
  // retry action; merely checking must never resend or invent a new command.
  const uncertainText='Unknown send '+width;await composer().fill(uncertainText);await composer().press('Enter');
  const unknown=await nextSend();await composer().fill(nextDraft);
  await unknown.route.fulfill({status:504,json:{error:'Fixture delivery uncertain',code:'runtime_pending'}});
  const uncertain=page.locator('.a-user').filter({hasText:uncertainText});
  await expect(uncertain.getByRole('alert')).toHaveText('Fixture delivery uncertain');await expect(uncertain.getByRole('alert')).toBeVisible();
  await expect(uncertain.getByRole('button',{name:'Retry',exact:true})).toHaveCount(0);
  await uncertain.getByRole('button',{name:'Check delivery',exact:true}).click();
  await expect(uncertain.getByRole('button',{name:'Retry original submission',exact:true})).toBeVisible();
  assert.equal(sendCount()-before,2,'Checking uncertain delivery must not resend');
  await expect(composer()).toHaveDraft(nextDraft);await expect(composer()).toContainText(nextDraft);await expect(composer()).toBeEditable();
  await expect(status()).toBeEmpty();row.unknown={checkVisible:true,originalRetryAvailable:true,sendPosts:sendCount()-before};
  await snap(width+'-unknown');
  // Resolve through authoritative SSE rather than retrying the failed model input.
  state.sessions[0].messages.push({id:'accepted-'+unknown.body.id,inputId:unknown.body.id,role:'user',
   text:uncertainText,delivery:{status:'accepted'}});await emit();
  await expect(uncertain.getByRole('button',{name:'Check delivery',exact:true})).toHaveCount(0);
  assert.equal(sendCount()-before,2);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No narrow overflow');
 }
 assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);
 proof.status='PASS';proof.sendPosts=sendCount();proof.pageErrors=errors;proof.blocked=blocked;
 await persist('result');
}
try{
 const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Caption harness exceeded 60s budget')),60000)});
 await Promise.race([run(),deadline]);
 console.log(JSON.stringify(proof,null,2));
}catch(error){
 proof.status='FAIL';proof.failure=error.stack;proof.pageErrors=errors;proof.blocked=blocked;
 await persist('result');console.error(JSON.stringify(proof,null,2));process.exitCode=1;
}finally{
 clearTimeout(timer);
 for(const item of held)try{await item.route.abort()}catch{}
 await browser?.close();await vite?.close();
}