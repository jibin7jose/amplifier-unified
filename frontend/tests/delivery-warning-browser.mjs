// Real bundled UI, synthetic HTTP/SSE only; no live chat or runtime.
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/delivery-warning';await mkdir(artifact,{recursive:true});
const message={id:'saved',inputId:'original',role:'user',text:'Keep this historical request and its reference.',attachments:[{id:'a',name:'reference.txt',size:12}],delivery:{status:'unknown',error:'Transport interrupted. Delivery is uncertain.'}};
const session=id=>({id,title:id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[structuredClone(message)],workers:[]});
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[session('chat'),session('other')],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
const shell=createShellHttpFixture(()=>state),calls=[],pending=[],errors=[];let vite,browser,page,holdChecks=false;
const outbox=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
const emit=async()=>{state.revision++;await page.evaluate(s=>window.emitState(s),shell.state())};
const original=()=>page.locator('[data-input-id="original"]');
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(installFixtureEvents);
 await page.addInitScript(()=>{if(!sessionStorage.getItem('fixture-seeded')){
  sessionStorage.setItem('fixture-seeded','1');sessionStorage.setItem('amplifier.messageOutbox.v1',JSON.stringify([{id:'local',commandId:'original',sessionId:'chat',scope:JSON.stringify(['fixture-shell-host','/fixture','chat']),text:'Keep this historical request and its reference.',attachmentIds:['a'],attachments:[{id:'a',name:'reference.txt',size:12}],status:'unknown',error:'Transport interrupted. Delivery is uncertain.'}]));
 }});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname,response=shell.response(route.request());
  if(response!==undefined)return route.fulfill({json:response});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=route.request().postDataJSON();calls.push(body);
  if(body.action==='conversation.delivery'){
   const target=state.sessions.find(session=>session.id===body.args.sessionId);
   const source={workspace:target.workspace||null,nativeIdentity:target.runtimeSessionId||target.nativeIdentity||target.id};
   if(holdChecks&&body.args.sessionId==='chat'){pending.push({route,body,source});return}
   return route.fulfill({json:{accepted:true,result:{...body.args,source,delivery:'unknown',message:'Still uncertain. Nothing was resent.'},state:shell.state()}});
  }
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.select')state.selectedSessionId=body.args.id;
  state.revision++;return route.fulfill({json:{accepted:true,state:shell.state()}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 await expect(original().getByRole('button',{name:'Dismiss warning',exact:true})).toBeEnabled();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.fill('Keep the next unsent thought');
 const before=await outbox(),effects=()=>calls.filter(c=>['conversation.send','conversation.retry','message.edit','session.delete'].includes(c.action));
 await page.screenshot({path:artifact+'/desktop-warning.png'});
 await original().getByRole('button',{name:'Dismiss warning',exact:true}).click();
 await expect(original().locator('summary')).toBeVisible();
 await expect(original().getByText('Delivery not confirmed',{exact:true})).not.toBeVisible();
 assert.deepEqual(await outbox(),before);assert.deepEqual(effects(),[]);
 await expect(composer).toHaveDraft('Keep the next unsent thought');await expect(original().getByText(/reference.txt/)).toBeVisible();
 await page.screenshot({path:artifact+'/desktop-collapsed.png'});
 await page.reload();await expect(original().locator('summary')).toBeVisible();
 await expect(original().getByText('Delivery not confirmed',{exact:true})).not.toBeVisible();
 await expect(composer).toHaveDraft('Keep the next unsent thought');
 await original().locator('summary').click();await original().getByRole('button',{name:'Show warning',exact:true}).click();
 await expect(original().getByRole('button',{name:'Dismiss warning',exact:true})).toBeVisible();
 // A pending read cannot be dismissed and cannot resend.
 holdChecks=true;
 await original().getByRole('button',{name:'Check delivery',exact:true}).click();
 await expect(original().getByRole('button',{name:'Dismiss warning',exact:true})).toBeDisabled();
 await expect.poll(()=>pending.length).toBe(1);
 const unknownCheck=pending.shift();
 await unknownCheck.route.fulfill({json:{accepted:true,result:{sessionId:'chat',inputId:'original',source:unknownCheck.source,delivery:'unknown',message:'Still uncertain. Nothing was resent.'},state:shell.state()}});
 holdChecks=false;
 await expect(original().getByRole('button',{name:'Dismiss warning',exact:true})).toBeEnabled();
 await original().getByRole('button',{name:'Dismiss warning',exact:true}).click();
 // Same input identity in another chat must not inherit local dismissal.
 state.selectedSessionId='other';await emit();
 await expect(original().getByRole('button',{name:'Dismiss warning',exact:true})).toBeVisible();
 state.selectedSessionId='chat';await emit();await expect(original().locator('summary')).toBeVisible();
 // Sending is never hidden even when the old warning was dismissed.
 state.sessions[0].messages[0].delivery={status:'sending'};await emit();
 await expect(original().getByText('Sending…',{exact:true})).toBeVisible();
 await expect(original().getByRole('button',{name:'Dismiss warning',exact:true})).toHaveCount(0);
 state.sessions[0].messages[0].delivery={...message.delivery};await emit();
 await page.setViewportSize({width:390,height:844});
 await expect(original().locator('summary')).toBeVisible();await page.screenshot({path:artifact+'/mobile-collapsed.png'});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await original().locator('summary').click();
 await page.screenshot({path:artifact+'/mobile-details.png'});
 // Receipt arrives after original leaves server window: retire exact outbox anyway.
 holdChecks=true;
 await original().getByRole('button',{name:'Check delivery',exact:true}).click();await expect.poll(()=>pending.length).toBe(1);
 state.selectedSessionId='other';await emit();
 await expect(original().getByRole('button',{name:'Check delivery',exact:true})).toBeEnabled();
 state.selectedSessionId='chat';await emit();
 await expect(original().getByRole('button',{name:'Checking…',exact:true})).toBeVisible();
 await expect(original().getByRole('button',{name:'Show warning',exact:true})).toBeDisabled();
 await original().locator('summary').click();
 await expect(original().getByRole('button',{name:'Checking…',exact:true})).toBeVisible();
 assert.equal(pending.length,1,'Remount does not issue a duplicate read');
 state.sessions[0].messages=[];state.revision++;
 const acceptedCheck=pending.shift();
 await acceptedCheck.route.fulfill({json:{accepted:true,result:{sessionId:'chat',inputId:'original',source:acceptedCheck.source,delivery:'accepted',message:'Received. Not resent.'},state:shell.state()}});
 await expect.poll(async()=>(await outbox()).length).toBe(0);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.deliveryWarning.v1:'+JSON.stringify(['chat','original']))),null);
 await expect(original()).toHaveCount(0);
 assert.deepEqual(effects(),[]);await expect(composer).toHaveDraft('Keep the next unsent thought');
 await page.reload();await expect.poll(async()=>(await outbox()).length).toBe(0);
 assert.deepEqual(errors,[]);
 await writeFile(artifact+'/browser-result.json',JSON.stringify({passed:true,calls:calls.filter(c=>c.action.startsWith('conversation.')),effects:effects(),errors,attachmentPreserved:true,exactWindowIndependentReceipt:true},null,2));
 console.log('Delivery warning browser passed: dismiss/restore/reload/chat isolation/sending fence/pending check/mobile/retained draft and attachment/exact receipt outside window/no resend.');
}finally{if(browser)await browser.close();if(vite)await vite.close()}
