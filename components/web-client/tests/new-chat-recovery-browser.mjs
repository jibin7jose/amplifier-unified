// Actual frontend, controlled offline receipts; never sends user work to a model.
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {createShellHttpFixture} from './shell-http-fixture.mjs';

let state={revision:1,settings:{workspace:'/existing'},runtime:{available:true},view:{navPinned:true,setupJourney:'done',newSessionDraft:{workspace:'/missing'}},
 sessions:[{id:'old',title:'Previous conversation',sessionKind:'root',workspace:'/existing',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
 workspaces:[{id:'project',name:'Existing',path:'/existing',available:true}],selectedSessionId:null,selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/existing'},
 registeredBundles:[{value:'work',label:'Work'}],draftDefaults:Object.fromEntries(['/missing','/existing'].map(workspace=>[JSON.stringify([workspace,'']),{phase:'ready',bundle:'work',effective:{instance:'fixture',model:'fixture-model'},providers:[{id:'fixture'}]}])),canvas:{open:false}};
const calls=[],workers=[],folders=[],errors=[];let browser,vite,page;
const shell=createShellHttpFixture(()=>state);
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const reply=(route,extra={})=>{state.revision++;return route.fulfill({json:{accepted:true,state:shell.state(),...extra}})};
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{ }),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  // The event stream owns bootstrap; /api/state alone no longer loads the app.
  window.EventSource=class extends EventTarget{constructor(){super();this.readyState=0;queueMicrotask(async()=>{const state=await fetch('/api/state').then(r=>r.json());if(this.readyState===2)return;this.readyState=1;this.onopen?.();this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}))})}close(){this.readyState=2}};
  if(sessionStorage.getItem('amplifier.messageOutbox.v1')===null)sessionStorage.setItem('amplifier.messageOutbox.v1',JSON.stringify([
   {id:'old-failed',commandId:'old-command',sessionId:null,text:'Hi!',attachmentIds:[],status:'failed',error:'Old workspace unavailable',createdAt:1}]));
 });
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const original=route.request().postDataJSON();calls.push(original);
  const body=original.action==='shell.command'?{...original,action:original.args.action,args:original.args.args}:original.action==='shell.view.update'?{...original,action:'view.update'}:original;
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.draft'){state.selectedSessionId=null;state.view.draft='';}
  if(body.action==='workspace.list')return reply(route,{result:{items:state.workspaces,nextOffset:null}});
  if(body.action==='session.create'){
   if(body.args.workspace!=='/existing')return route.fulfill({status:409,json:{accepted:false,error:'Workspace folder does not exist: /missing'}});
   const chat={...state.sessions[0],id:'created',title:'Created conversation',messages:[],creationCommandId:body.id};
   state.sessions.unshift(chat);state.selectedSessionId=chat.id;delete state.view.newSessionDraft;
  }
  if(body.action==='conversation.send'){
   state.sessions.find(row=>row.id===body.args.sessionId).messages.push({id:'received',inputId:body.id,role:'user',text:body.args.text,delivery:{status:'accepted'}});
  }
  if(body.action==='worker.spawn'){workers.push({route,body});return;}
  if(body.action==='locations.list'){
   state.locationListing={controlId:body.args.controlId,path:body.args.path||'/existing',parent:'/',entries:[]};
   state.actionStatus={'locations.list':{phase:'ready',commandId:body.id,target:{controlId:body.args.controlId}}};
  }
  if(body.action==='locations.create'){folders.push({route,body});return;}
  return reply(route);
 });
 await page.goto(vite.resolvedUrls.local[0]);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await expect(page.getByRole('banner')).toContainText('New chat');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),null);
 await expect(page.getByText('Old workspace unavailable',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Discard unsent message',exact:true}).click();
 await expect(page.getByText('Hi!',{exact:true})).toHaveCount(0);await page.reload();await composer.waitFor();
 await expect(page.getByText('Hi!',{exact:true})).toHaveCount(0);
 assert.equal(calls.filter(row=>['conversation.send','session.create'].includes(row.action)).length,0,'Discard is local only');
 await composer.fill('Start fresh');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Workspace folder does not exist: /missing',{exact:true})).toBeVisible();
 await expect(page.getByRole('banner')).toContainText('New chat');
 await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
 await page.getByRole('button',{name:'Existing · /existing',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Choose a workspace',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Retry',exact:true}).click();
 await expect(page.getByText('Start fresh',{exact:true})).toHaveCount(1);
 await expect(page.getByRole('banner')).toContainText('Created conversation');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),'created');
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);
 assert.equal(calls.filter(row=>row.action==='conversation.send').length,1);
 assert.deepEqual(calls.filter(row=>row.action==='session.create').map(row=>row.args.workspace),['/missing','/existing']);
 const creations=calls.filter(row=>row.action==='session.create');
 assert.notEqual(creations[0].id,creations[1].id,'A definite rejection retries the corrected setup under a new command ID');
 assert.equal(calls.find(row=>row.action==='conversation.send').args.sessionId,'created');
 await action('view.update',{patch:{panel:'worker'}});
 const worker=page.getByLabel('Work to delegate');await worker.fill('First worker request');await page.getByRole('button',{name:'Start worker',exact:true}).click();
 await expect.poll(()=>workers.length).toBe(1);await worker.fill('A newer worker draft');await reply(workers.shift().route);
 await expect(worker).toHaveValue('A newer worker draft');await expect(page.getByRole('dialog')).toBeVisible();
 await page.getByRole('button',{name:'Start worker',exact:true}).click();await expect.poll(()=>workers.length).toBe(1);
 await workers.shift().route.fulfill({status:409,json:{accepted:false,error:'Worker rejected for fixture'}});
 await expect(worker).toHaveValue('A newer worker draft');
 await page.getByRole('dialog').getByRole('button',{name:'Dismiss error',exact:true}).click();
 await expect(page.getByText('Worker rejected for fixture',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Start worker',exact:true}).click();await expect.poll(()=>workers.length).toBe(1);await reply(workers.shift().route);
 await expect(page.getByRole('dialog')).toHaveCount(0);assert.equal(state.view.workerDraft,'');
 await page.reload();await composer.waitFor();await action('view.update',{patch:{panel:'worker'}});await expect(worker).toHaveValue('');
 await worker.fill('Submitted worker instruction');await page.getByRole('button',{name:'Start worker',exact:true}).click();
 await expect.poll(()=>workers.length).toBe(1);const sharedWorker=workers.shift();
 assert.equal(sharedWorker.body.args.instruction,'Submitted worker instruction');
 // An agent's shared-view update arrives with the delayed submission receipt.
 state.view={...state.view,workerDraft:'A newer shared worker draft'};await reply(sharedWorker.route);
 await expect(worker).toHaveValue('A newer shared worker draft');await expect(page.getByRole('dialog')).toBeVisible();
 assert.equal(state.view.workerDraft,'A newer shared worker draft');
 await action('view.update',{patch:{panel:null}});await page.getByRole('button',{name:'New chat',exact:true}).click();
 await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
 await page.getByRole('button',{name:'Use existing folder',exact:true}).click();
 await page.getByRole('form',{name:'Use existing folder',exact:true}).getByRole('button',{name:'Browse',exact:true}).click();
 await page.getByRole('button',{name:'New folder',exact:true}).click();
 const folder=page.getByRole('textbox',{name:'New folder name',exact:true});
 await folder.fill('first-folder');await page.getByRole('button',{name:'Create folder',exact:true}).click();
 await expect.poll(()=>folders.length).toBe(1);await expect(folder).toBeDisabled();
 await folders.shift().route.fulfill({status:409,json:{accepted:false,error:'Folder denied for fixture'}});
 await expect(folder).toBeEnabled();await expect(folder).toHaveValue('first-folder');
 await expect(page.getByText('Folder denied for fixture',{exact:true})).toBeVisible();
 await folder.fill('created-folder');await page.getByRole('button',{name:'Create folder',exact:true}).click();
 await expect.poll(()=>folders.length).toBe(1);const creation=folders.shift();
 assert.equal(creation.body.args.name,'created-folder');
 const createdPath=creation.body.args.path+'/created-folder';
 state.locationListing={controlId:creation.body.args.controlId,path:createdPath,parent:creation.body.args.path,entries:[],createdBy:creation.body.id};
 state.actionStatus['locations.create']={phase:'ready',commandId:creation.body.id};
 await reply(creation.route,{operationId:creation.body.id});
 await expect(folder).toHaveCount(0);
 await expect(page.getByRole('textbox',{name:'Folder path',exact:true})).toHaveValue(createdPath);
 await page.getByRole('button',{name:'Use this folder',exact:true}).click();
 await expect(page.getByRole('textbox',{name:/Folder on/})).toHaveValue(createdPath);
 assert.equal(calls.filter(row=>row.action==='conversation.send').length,1,'Folder selection does not send a new message');
 assert.deepEqual(errors,[]);
 console.log('New chat recovery passed: truthful new-chat header; persisted failed-message discard; inline creation error; corrected-path retry sends once; worker success persists reset; failure/local and shared newer drafts retained; modal error dismissal. Zero model calls.');
}catch(error){console.error('Recovery fixture evidence:',JSON.stringify({errors,body:await page?.locator('body').innerText().catch(()=>''),actions:calls.map(row=>({action:row.action,id:row.id}))}));throw error}
finally{await browser?.close();await vite?.close()}
