// Built client with bounded synthetic state. No runtime or provider calls.
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {preview} from 'vite';
import {chromium,expect} from '@playwright/test';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';

const message=(id,role='assistant',text=id)=>({id,role,text,createdAt:1700000000});
const session={id:'refresh-chat',sessionKind:'root',title:'Refresh fixture',workspace:'/fixture',workspaceId:'project',
 status:'idle',historyLoaded:false,historyLoading:true,messages:[],execution:{nodes:[],turns:[]},
 messageWindow:{mode:'cursor',hasEarlier:false,before:null},workProjection:{status:'loading',current:false}};
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},
 view:{setupJourney:'done',workSurface:'chat',navPinned:true},sessions:[session],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],
 selectedSessionId:session.id,selectedWorkspaceId:'project',canvas:{open:false}};
const shell=createShellHttpFixture(()=>state),errors=[],evidence={checks:[],frames:[]};
const receipt={hostInstanceId:'fixture-shell-host',sessionId:session.id,generation:1,sourceCount:1,
 scope:'recent-events',windowKey:'fixture-work',historyComplete:false,itemCount:0,scannedBytes:0,scannedEvents:0};
let server,browser,page,releaseEarlier,releaseSend,sendBody,earlierReads=0;
try{
 server=await preview({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  build:{outDir:fileURLToPath(new URL('../../amplifier_web/static',import.meta.url))},
  preview:{host:'127.0.0.1',port:0}});
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:950}});page.setDefaultTimeout(10000);
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname,response=shell.response(route.request());
  if(response!==undefined)return route.fulfill({json:response});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/conversation/detail'){
   const query=new URL(route.request().url()).searchParams;
   assert.equal(query.get('sessionId'),session.id);assert.equal(query.get('part'),'messages');
   assert.equal(query.get('before'),'fixture-earlier');earlierReads++;
   await new Promise(resolve=>{releaseEarlier=resolve});
   return route.fulfill({json:{items:[message('older-message','user','An earlier saved request.')],
    mode:'cursor',before:null,hasEarlier:false,windowKey:'earlier-page'}}).catch(()=>{});
  }
  if(path==='/api/outputs/media')return route.fulfill({json:{items:[],overflow:false,mediaRevision:0}});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path==='/api/actions'){
   const envelope=route.request().postDataJSON(),body=envelope.action==='shell.command'?envelope.args:envelope;
   if(body.action==='conversation.send'){sendBody=body;await new Promise(resolve=>{releaseSend=resolve})}
   if(body.action==='view.update')state={...state,revision:state.revision+1,view:{...state.view,...body.args.patch}};
   return route.fulfill({json:{accepted:true,operationId:envelope.id,state:shell.state()}});
  }
  return route.fulfill({json:{ok:true}});
 });
 await page.goto(server.resolvedUrls.local[0]);
 await expect(page.getByText('Loading conversation…',{exact:true})).toBeVisible();
 await expect(page.locator('[data-message-id]')).toHaveCount(0);
 evidence.checks.push('initial transcript loading remains visible');
 const paint=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const publish=async patch=>{Object.assign(session,patch);state={...state,revision:state.revision+1};await page.evaluate(value=>window.emitState(value),shell.state());await paint()};
 await publish({historyLoaded:true,historyLoading:false,messages:[message('visible-message','user','Keep this request in place.')],
  workProjection:{status:'ready',current:true,receipt}});
 const pane=page.locator('.a-messages'),composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(page.locator('[data-message-id="visible-message"]')).toBeVisible();await expect(composer).toBeEnabled();
 // Observe the actual DOM before the next paint as well as settled geometry.
 await pane.evaluate(element=>{
  const first=element.querySelector('[data-message-id="visible-message"]');
  const rect=node=>{const b=node.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height}};
  const snapshot=()=>({message:rect(first),pane:rect(element),composer:rect(document.querySelector('[aria-label="Message Amplifier"]')),
   notice:!!element.querySelector('[data-part="work-projection-status"], [data-part="session-history"]'),
   scrollTop:element.scrollTop});
  window.refreshProbe={snapshot,baseline:snapshot(),frames:[]};
  window.refreshObserver=new MutationObserver(()=>{if(window.refreshProbe.frames.length<200)window.refreshProbe.frames.push(snapshot())});
  window.refreshObserver.observe(element,{subtree:true,childList:true,attributes:true,attributeFilter:['data-work-projection']});
 });
 const assertStable=async label=>{
  const sample=await page.evaluate(()=>({baseline:refreshProbe.baseline,current:refreshProbe.snapshot(),frames:refreshProbe.frames.splice(0)}));
  evidence.frames.push({label,...sample});
  for(const current of [...sample.frames,sample.current]){
   assert.equal(current.notice,false,label+': background refresh inserted a history-loading row');
   for(const name of ['message','pane','composer'])for(const key of ['x','y','width','height'])
    assert.ok(Math.abs(current[name][key]-sample.baseline[name][key])<1,label+': '+name+' '+key+' moved');
   assert.equal(current.scrollTop,sample.baseline.scrollTop,label+': background refresh changed scroll position');
  }
 };
 for(let i=0;i<3;i++){
  await publish({historyLoading:true,workProjection:{status:'loading',current:false,receipt}});
  await assertStable('retained refresh '+i);
  assert.equal(await pane.evaluate(el=>JSON.parse(el.dataset.workProjection).current),false,'Hidden notice must not imply a ready receipt');
  await publish({historyLoading:false,workProjection:{status:'ready',current:true,receipt:{...receipt,generation:i+2}}});
  await assertStable('refresh completed '+i);
 }
 // First voice text can arrive before the first saved Work receipt exists.
 await publish({messages:[...session.messages,{...message('voice-fragment','user','A new spoken request.'),via:'call',voiceId:'fixture-call'}],
  workProjection:{status:'loading',current:false}});
 await expect(page.locator('[data-message-id="voice-fragment"]')).toBeVisible();
 await expect(pane.locator('[data-part="work-projection-status"]')).toHaveCount(0);
 await assertStable('first spoken fragment');
 await publish({streaming:'A live response is arriving.'});
 await expect(pane).toContainText('A live response is arriving.');
 await assertStable('streaming response');
 evidence.checks.push('background refresh stays silent without claiming readiness','new spoken messages need no saved Work receipt');
 await page.evaluate(()=>refreshObserver.disconnect());
 // A legacy/current manager hydration flag cannot erase already readable text.
 await publish({historyLoaded:false,historyLoading:true,messageWindow:undefined,streaming:''});
 await expect(page.locator('[data-message-id="visible-message"]')).toBeVisible();
 await expect(page.getByText('Loading conversation…',{exact:true})).toHaveCount(0);
 await expect(composer).toBeEnabled();
 evidence.checks.push('retained text survives canonical hydration');
 await publish({historyLoaded:true,historyLoading:false,workProjection:{status:'ready',current:true,receipt},
  messageWindow:{mode:'cursor',hasEarlier:true,before:'fixture-earlier'}});
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect.poll(()=>earlierReads).toBe(1);
 await expect(page.getByRole('button',{name:'Loading earlier conversation…',exact:true})).toBeDisabled();
 await expect(page.locator('[data-message-id="visible-message"]')).toBeVisible();
 releaseEarlier();await expect(page.locator('[data-message-id="older-message"]')).toBeVisible();
 await expect(page.getByRole('button',{name:'Return to latest',exact:true})).toBeEnabled();
 assert.equal(earlierReads,1);evidence.checks.push('intentional older history retains loading feedback and completes once');
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();
 // A stream or an optimistic outgoing message can be the only readable text.
 await publish({messages:[],streaming:'First streamed reply.',historyLoading:true,
  messageWindow:{mode:'cursor',hasEarlier:false,before:null},workProjection:{status:'loading',current:false}});
 await expect(pane).toContainText('First streamed reply.');
 await expect(pane.locator('[data-part="session-history"], [data-part="work-projection-status"]')).toHaveCount(0);
 await publish({streaming:'',historyLoaded:true,historyLoading:false,workProjection:{status:'ready',current:true,receipt}});
 await composer.fill('A local pending request.');await composer.press('Enter');
 await expect.poll(()=>!!sendBody).toBe(true);
 await publish({historyLoaded:false,historyLoading:true,workProjection:{status:'loading',current:false}});
 await expect(pane).toContainText('A local pending request.');
 await expect(pane.locator('[data-part="session-history"], [data-part="work-projection-status"]')).toHaveCount(0);
 const sendReceipt=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action==='conversation.send');
 releaseSend();await sendReceipt;
 evidence.checks.push('stream-only and unacknowledged local input stay readable without a loading row');
 assert.deepEqual(errors,[]);evidence.status='passed';
 console.log(JSON.stringify({status:evidence.status,checks:evidence.checks,pageErrors:errors}));
}catch(error){evidence.status='failed';evidence.error=error.message;throw error}
finally{
 releaseEarlier?.();releaseSend?.();
 if(process.env.AMPLIFIER_TEST_ARTIFACTS){
  await mkdir(process.env.AMPLIFIER_TEST_ARTIFACTS,{recursive:true});
  await writeFile(process.env.AMPLIFIER_TEST_ARTIFACTS+'/history-refresh.json',JSON.stringify({...evidence,pageErrors:errors},null,2)+'\n');
  await page?.screenshot({path:process.env.AMPLIFIER_TEST_ARTIFACTS+'/history-refresh.png'}).catch(()=>{});
 }
 await browser?.close();await new Promise(resolve=>server?server.httpServer.close(resolve):resolve());
}
