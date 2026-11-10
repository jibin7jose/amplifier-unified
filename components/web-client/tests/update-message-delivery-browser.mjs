import './composer-test-helpers.mjs';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

// Mounted client with controlled HTTP/state ordering. Backend checkpoint/release
// behavior has separate native coverage; this fixture never executes a message.
const state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},
 sessions:[{id:'chat',title:'Update delivery',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
const shell=createShellHttpFixture(()=>state),waiting=[],calls=[],errors=[],cases=[];
const evidence=process.env.AMPLIFIER_TEST_ARTIFACTS;
let browser,vite,page;
const chat=()=>state.sessions[0];
const outbox=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
const bubble=id=>page.locator(`[data-input-id="${id}"]`);
const composer=()=>page.getByRole('textbox',{name:'Message Amplifier'});
async function publish(){state.revision++;await page.evaluate(value=>window.emitState(value),shell.state())}
async function send(text){
 await composer().fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(()=>waiting.length).toBe(1);
 const item=waiting.shift();assert.equal(item.body.args.text,text);
 await expect(bubble(item.body.id)).toHaveCount(1);
 return item;
}
function retain(item){
 const message={id:'saved-'+item.body.id,inputId:item.body.id,role:'user',text:item.body.args.text,
  createdAt:Date.now()/1000,delivery:{status:'queued_for_update'}};
 chat().messages.push(message);return message;
}
async function saved(item,label){
 const row=bubble(item.body.id);
 await expect(row).toHaveCount(1);
 await expect(row.locator('.a-message-delivery[role="status"]')).toHaveText('Saved · waiting for the app update. You do not need to send it again.');
 await expect(row.getByRole('button',{name:/Retry|Check delivery|Send again|Discard unsent/})).toHaveCount(0);
 assert.doesNotMatch(await row.innerText(),/Not sent|Delivery not confirmed/);
 await expect.poll(outbox).toEqual([]);
 assert.equal(calls.filter(call=>call.id===item.body.id).length,1,'The original submission is not replayed');
 cases.push({case:label,inputId:item.body.id,renderedCopies:1,posts:1,outboxEntries:0});
}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  cacheDir:fileURLToPath(new URL('../.vite-update-message-fixture',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,response=shell.response(request);
  if(response!==undefined)return route.fulfill({json:response});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=request.postDataJSON();calls.push(body);
  if(body.action==='conversation.send'){waiting.push({route,body});return}
  assert.ok(!['conversation.retry','conversation.delivery'].includes(body.action),'Saved update input must not request retry or delivery recovery');
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  state.revision++;return route.fulfill({json:{accepted:true,state:shell.state()}});
 });
 await page.goto(vite.resolvedUrls.local[0]);await composer().waitFor();

 const acknowledged=await send('Keep this message through the update.');retain(acknowledged);state.revision++;
 await acknowledged.route.fulfill({json:{accepted:true,delivery:'queued_for_update',state:shell.state()}});
 await saved(acknowledged,'known queued acknowledgment');

 const stateFirst=await send('The saved state arrives before the HTTP failure.');retain(stateFirst);await publish();
 await saved(stateFirst,'canonical receipt precedes lost acknowledgment');
 await stateFirst.route.abort('failed');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions[0].messages.length)).toBe(2);
 await saved(stateFirst,'late transport failure does not recreate outbox');

 const ackFirst=await send('The HTTP failure arrives before the saved state.');await ackFirst.route.abort('failed');
 await expect(bubble(ackFirst.body.id).getByText('Delivery not confirmed',{exact:false})).toBeVisible();
 assert.equal((await outbox())[0].commandId,ackFirst.body.id);
 retain(ackFirst);await publish();await saved(ackFirst,'canonical receipt resolves uncertain outbox');

 const cold=await send('Reload with the original saved input identity.');await cold.route.abort('failed');
 await expect(bubble(cold.body.id).getByText('Delivery not confirmed',{exact:false})).toBeVisible();
 assert.equal((await outbox())[0].commandId,cold.body.id);
 // The server saved the input while the client was disconnected. Bootstrap on
 // reload must reconcile its retained local entry without posting it again.
 retain(cold);state.revision++;
 await composer().fill('Keep this newer unsent draft.');
 await page.reload();await composer().waitFor();
 await saved(cold,'refresh reconciles original identity');
 await expect(composer()).toHaveDraft('Keep this newer unsent draft.');
 for(const item of [acknowledged,stateFirst,ackFirst,cold])await expect(bubble(item.body.id)).toHaveCount(1);

 const stoppedWhileDisconnected=await send('Stop retains this input before its acknowledgment arrives.');
 await stoppedWhileDisconnected.route.abort('failed');
 await expect(bubble(stoppedWhileDisconnected.body.id).getByText('Delivery not confirmed',{exact:false})).toBeVisible();
 const disconnectedMessage=retain(stoppedWhileDisconnected);
 chat().status='stopped';
 const held=chat().messages.find(message=>message.inputId===acknowledged.body.id);
 held.delivery={status:'held',reason:'Stopped after this message was saved. Nothing was sent.'};
 disconnectedMessage.delivery={...held.delivery};state.revision++;
 // This client never saw queued_for_update. The canonical held receipt still
 // acknowledges saving, while explicitly declining delivery after Stop.
 await page.reload();await composer().waitFor();
 await page.setViewportSize({width:390,height:844});
 for(const item of [acknowledged,stoppedWhileDisconnected]){
  await expect(bubble(item.body.id)).toHaveCount(1);
  await expect(bubble(item.body.id).locator('.a-message-delivery[role="status"]')).toHaveText(held.delivery.reason);
  await expect(bubble(item.body.id).getByRole('button',{name:/Retry|Send again|Check delivery/})).toHaveCount(0);
  assert.doesNotMatch(await bubble(item.body.id).innerText(),/Added to the active run|Received by the active run|waiting for the app update/);
 }
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Phone status remains in the viewport');
 await expect.poll(outbox).toEqual([]);
 cases.push({case:'Stop-held remains saved without claiming delivery',inputId:acknowledged.body.id,status:'held'});
 cases.push({case:'lost acknowledgment then Stop reconciles retained outbox on refresh',inputId:stoppedWhileDisconnected.body.id,status:'held'});
 assert.equal(calls.filter(call=>call.action==='conversation.send').length,5);
 assert.equal(new Set(chat().messages.map(message=>message.inputId)).size,5);
 assert.deepEqual(errors,[]);
 if(evidence){await mkdir(evidence,{recursive:true});await page.screenshot({path:evidence+'/held-mobile.png'});
  await writeFile(evidence+'/receipt.json',JSON.stringify({cases,sendPosts:5,distinctSavedInputs:5,pageErrors:errors,backendExecution:'synthetic HTTP/state fixture; separately qualified'},null,2)+'\n')}
 console.log('PASS: update-time saved status, both lost-ack orders, refresh with original identities and draft, no replay, and Stop-held mobile explanation.');
}finally{if(browser)await browser.close();if(vite)await vite.close()}
