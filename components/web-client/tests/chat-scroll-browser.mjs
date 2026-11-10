import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
// Uses the real chat UI and isolated runtime. No model or existing user data.
// Vite serves source directly, so this regression does not rebuild static assets.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u','-c',`
import asyncio, json, signal, sys, tempfile
from pathlib import Path
from aiohttp import web
sys.path.insert(0, ${JSON.stringify(fileURLToPath(new URL('../../tests/fixtures',import.meta.url)))})
import chat_ui_server as fixture
ack_rows=[]
def observe_ack(phase, input_id, session_id, **facts):
 try:
  if len(ack_rows)<32:
   row={'phase':phase,'inputId':input_id,'sessionId':session_id,**facts}
   ack_rows.append(row);print('SCROLL-FIXTURE-ACK '+json.dumps(row),flush=True)
 except Exception:pass
class ScrollRuntime(fixture.ChatRuntime):
 async def send(self, session, text, input_id, emit):
  self.sent.append({'sessionId':session['id'],'text':text,'inputId':input_id})
  observe_ack('runtime-entered',input_id,session['id'])
  await emit('runtime.status', {'sessionId':session['id'], 'status':'working'})
  observe_ack('working-returned',input_id,session['id'],status=session.get('status'))
  # Work stays controlled by /grow. send acknowledges admission, not completion.
  await emit('runtime.status', {'sessionId':session['id'], 'status':'working',
   'event':'input.delivered','inputId':input_id})
  observe_ack('input-delivered-returned',input_id,session['id'],status=session.get('status'))
  observe_ack('ack-returning',input_id,session['id'],accepted=True)
  return {'accepted':True,'inputId':input_id}
fixture.fixture.Runtime=ScrollRuntime
async def main():
 stopped=asyncio.Event()
 loop=asyncio.get_running_loop()
 for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stopped.set)
 with tempfile.TemporaryDirectory(prefix='amplifier-scroll-ui-') as home:
  app=await fixture.fixture.main(Path(home))
  service=app['service']
  @web.middleware
  async def observe_send(request,handler):
   if request.path!='/api/actions' or request.method!='POST':return await handler(request)
   body=await request.json()
   if body.get('action')!='conversation.send':return await handler(request)
   input_id=body.get('id');sid=body.get('args',{}).get('sessionId')
   observe_ack('request-entered',input_id,sid)
   try:
    response=await handler(request)
   except BaseException as error:
    observe_ack('request-error',input_id,sid,errorClass=type(error).__name__);raise
   observe_ack('request-returned',input_id,sid,httpStatus=response.status)
   return response
  app.middlewares.append(observe_send)
  session=service.state['sessions'][0]
  session['messages']=[{'id':f'history-{i}', 'role':'user' if i%2==0 else 'assistant', 'text':f'History {i}\\n\\n'+('A useful paragraph. '*35), 'createdAt':i+1} for i in range(30)]
  async def grow(request):
   await service.on_runtime_event('assistant.delta', {'sessionId':session['id'], 'text':'\\n\\nMore streaming content. '*25})
   return web.json_response({'ok':True})
  app.router.add_post('/api/fixture/grow', grow)
  async def hidden_reply(request):
   await service.on_runtime_event('assistant.message', {'sessionId':session['id'], 'text':'Fresh reply received while this tab was hidden.'})
   return web.json_response({'ok':True})
  app.router.add_post('/api/fixture/hidden-reply', hidden_reply)
  runner=web.AppRunner(app)
  try:
   await runner.setup()
   site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
   port=site._server.sockets[0].getsockname()[1]
   app['allowed_origins']=app['allowed_origins']|{f'http://127.0.0.1:{port}'}
   print(json.dumps({'port':port}),flush=True)
   await stopped.wait()
  finally:await runner.cleanup()
asyncio.run(main())
`],{stdio:['ignore','pipe','pipe']});
const stopFixture=ownFixture(fixture,{label:'Scroll fixture'});
const fixtureReceipts={exit:null,close:null};
for(const event of ['exit','close'])fixture.once(event,(code,signal)=>{fixtureReceipts[event]={pid:fixture.pid,code,signal}});
let fixtureLog='',fixtureOutput='';const fixtureAcks=[],sendTrace=[],transports=[];
const acceptedCanonical=(messages,inputId)=>messages.length===1&&messages[0].role==='user'&&
 messages[0].inputId===inputId&&messages[0].id!==inputId&&messages[0].delivery==='accepted';
const heldSendRoutes=new Map();let routeAbortFailed=false;
const abortHeldSend=async route=>{
 if(!heldSendRoutes.has(route))return;
 const inputId=heldSendRoutes.get(route);heldSendRoutes.delete(route);
 try{await route.abort('failed');sendTrace.push({phase:'aborted',inputId})}
 catch(error){routeAbortFailed=true;sendTrace.push({phase:'abort-gap',inputId,errorClass:error.name})}
};
fixture.stderr.on('data',chunk=>{fixtureLog=(fixtureLog+chunk).slice(-8192)});
fixture.stdout.on('data',chunk=>{
 fixtureOutput+=chunk;
 const lines=fixtureOutput.split('\n');fixtureOutput=lines.pop();
 for(const line of lines)if(line.startsWith('SCROLL-FIXTURE-ACK ')&&fixtureAcks.length<32){
  try{fixtureAcks.push(JSON.parse(line.slice('SCROLL-FIXTURE-ACK '.length)))}catch{}
 }
});
const fixtureReady=new Promise((resolve,reject)=>{
 let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('exit',code=>reject(new Error(`Scroll fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page,primaryFailure;
// Read-only observation is armed before send setup, so the first failed wait
// cannot prevent capture. No diagnostic call changes scroll or schedules a frame.
const markScrollFlow=async phase=>{
 try{await page.evaluate(phase=>window.scrollFlowEvidence?.mark(phase),phase)}
 catch(error){try{console.error('SCROLL-FLOW-EVIDENCE-GAP',phase,error.name)}catch{}}
};
const finishScrollFlow=async outcome=>{
 try{
  const evidence=await page?.evaluate(outcome=>window.scrollFlowEvidence?.finish(outcome),outcome);
  if(evidence)console.log('SCROLL-SEND-TO-ANCHOR',JSON.stringify(evidence));
 }catch(error){try{console.error('SCROLL-FLOW-EVIDENCE-GAP','finish',error.name)}catch{}}
};
try{
 const port=await Promise.race([fixtureReady,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Scroll fixture did not start')),15000).unref())]);
 const target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('websocket',socket=>{if(transports.length<4)transports.push({kind:'websocket',path:new URL(socket.url()).pathname})});
 page.on('response',response=>{if(response.headers()['content-type']?.includes('text/event-stream')&&transports.length<4)
  transports.push({kind:'eventsource',path:new URL(response.url()).pathname})});
 const requests=[];page.on('request',request=>requests.push(new URL(request.url()).pathname));
 await page.goto(vite.resolvedUrls.local[0]);await showFixtureConversation(page);await page.waitForSelector('#amp-one');
 const pane=page.locator('.a-messages'),input=page.getByRole('textbox',{name:'Message Amplifier'});
 // Capture a settled frame and preserve the reader's message/offset; raw
 // scrollTop is invariant only when that message's content position is too.
 const readingPosition=()=>pane.evaluate(element=>new Promise((resolve,reject)=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
  const bounds=element.getBoundingClientRect(),top=bounds.top,rows=[...element.querySelectorAll('[data-message-id]')];
  const anchor=rows.find(row=>row.getBoundingClientRect().bottom>top)||rows.at(-1);
  if(!anchor)return reject(Error('No conversation message for reading-position assertion'));
  const offset=anchor.getBoundingClientRect().top-top;
  // An uncommitted streamed reply has no message ID yet. Also retain its
  // first visible paragraph within the reply owned by the submitted message.
  const paragraphs=[...element.querySelectorAll(':scope > .a-assistant:not([data-message-id]) .a-message-body p')];
  const index=paragraphs.findIndex(node=>{const box=node.getBoundingClientRect();return box.bottom>top&&box.top<bounds.bottom});
  const paragraph=index<0?null:paragraphs[index];
  if(!paragraph)return reject(Error('No visible streaming paragraph for reading-position assertion'));
  resolve({id:anchor.dataset.messageId,offset,scrollTop:element.scrollTop,contentTop:offset+element.scrollTop,
   stream:{owner:rows.at(-1)?.dataset.messageId,index,offset:paragraph.getBoundingClientRect().top-top,text:paragraph.textContent},
   notice:document.querySelector('[data-part="work-projection-status"]')?.textContent||null});
 }))));
 const assertReading=(before,after,message)=>{
  const detail=message+': '+JSON.stringify({before,after});
  assert.equal(after.id,before.id,detail+' (message)');
  assert.ok(Math.abs(after.offset-before.offset)<3,detail+' (visible offset)');
  if(before.stream){
   assert.equal(after.stream?.owner,before.stream.owner,detail+' (stream owner)');
   assert.equal(after.stream?.index,before.stream.index,detail+' (stream paragraph)');
   assert.equal(after.stream?.text,before.stream.text,detail+' (stream text)');
   assert.ok(Math.abs(after.stream.offset-before.stream.offset)<3,detail+' (visible stream offset)');
  }
  if(Math.abs(after.contentTop-before.contentTop)<3)
   assert.ok(Math.abs(after.scrollTop-before.scrollTop)<3,detail+' (stable geometry)');
 };
 const readingChecks=[];
 const checkReading=async(before,message)=>{
  const after=await readingPosition();assertReading(before,after,message);readingChecks.push({message,before,after});
 };
 const atBottom=()=>page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 const workReady=()=>page.waitForFunction(()=>{
  const pane=document.querySelector('.a-messages');
  try{const receipt=JSON.parse(pane?.dataset.workProjection);return receipt.status==='ready'&&receipt.current===true&&!pane.querySelector('[data-part="work-projection-status"]')}catch{return false}
 });
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const scrollBack=async()=>{const box=await pane.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-1200);await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight>500});await page.waitForTimeout(150)};
 await atBottom();
 assert.equal(requests.filter(path=>path==='/api/state').length,0,'stream supplies the initial baseline without a duplicate GET');
 // The rail previews one turn, keeps bookmarks across reload, and jumps without
 // enabling scroll-follow. Metadata is quiet until hover/focus in Balanced.
 const marks=page.locator('.a-rail-mark');assert.equal(await marks.count(),15);
 await expect(page.getByRole('navigation',{name:'Chat navigator'})).not.toHaveAttribute('title');
 await expect(marks.last()).toHaveAttribute('data-visible','true');
 // Preview geometry follows the actual mark, including the short marks list
 // inside a taller rail; hovering never changes the reading-position highlight.
 for(const index of [0,7,14]){
  await marks.nth(index).hover();
  const preview=page.locator('.a-rail-preview');await preview.waitFor();
  await expect.poll(async()=>{
   const anchor=await marks.nth(index).boundingBox(),box=await preview.boundingBox(),container=await page.locator('.a-conversation').boundingBox();
   const top=Math.max(8,container.y+8),bottom=Math.min(900-8,container.y+container.height-8)-box.height;
   return Math.abs(box.y-Math.max(top,Math.min(bottom,anchor.y+anchor.height/2-box.height/2)));
  }).toBeLessThan(2);
  await expect(marks.last()).toHaveAttribute('data-visible','true');
 }
 await page.setViewportSize({width:1280,height:600});
 await marks.last().hover();
 await expect.poll(async()=>{const box=await page.locator('.a-rail-preview').boundingBox();return box.y>=8&&box.y+box.height<=592}).toBe(true);
 await page.setViewportSize({width:1280,height:900});
 await marks.nth(3).hover();await page.locator('.a-rail-preview').waitFor();
 assert.match(await page.locator('.a-rail-preview').innerText(),/History 6[\s\S]*History 7/);
 assert.equal(await page.locator('.a-rail-preview-jump').evaluate(node=>{const style=getComputedStyle(node);return style.whiteSpace==='nowrap'&&style.textOverflow==='ellipsis'&&node.scrollWidth>node.clientWidth}),true);
 await page.getByRole('button',{name:'Bookmark message',exact:true}).click();
 assert.equal(await marks.nth(3).getAttribute('data-bookmarked'),'true');
 await marks.nth(3).focus();await marks.nth(3).press('ArrowDown');
 assert.equal(await marks.nth(4).evaluate(n=>n===document.activeElement),true);
 await marks.nth(4).press('ArrowUp');
 await marks.nth(3).click();
 const targetMessage=pane.locator('[data-message-id="history-6"]');
 await expect(marks.nth(3)).toHaveAttribute('data-visible','true');
 await expect(marks.last()).toHaveAttribute('data-visible','false');
 // A partially visible response and the following user turn both light up.
 await pane.evaluate(element=>{const node=element.querySelector('[data-message-id="history-7"]');element.scrollTop+=node.getBoundingClientRect().top-element.getBoundingClientRect().top});
 await expect(marks.nth(3)).toHaveAttribute('data-visible','true');
 await expect(marks.nth(4)).toHaveAttribute('data-visible','true');
 await marks.nth(3).click();
 assert.ok(Math.abs((await targetMessage.boundingBox()).y-(await pane.boundingBox()).y-16)<3);
 await input.focus();await page.mouse.move(0,0);await page.waitForTimeout(200);
 assert.equal(await targetMessage.locator('.a-message-actions').evaluate(n=>getComputedStyle(n).opacity),'0');
 await targetMessage.hover();await page.waitForTimeout(200);
 assert.equal(await targetMessage.locator('.a-message-actions').evaluate(n=>getComputedStyle(n).opacity),'1');
 assert.equal(await targetMessage.locator('time').getAttribute('title'),null);
 assert.ok(await targetMessage.locator('time').getAttribute('aria-label'));
 assert.equal(await targetMessage.locator('.a-message-actions > :last-child').evaluate(node=>node.tagName),'TIME');
 await targetMessage.locator('time').hover();await expect(page.getByRole('tooltip')).toHaveCount(0);
 const copyButton=targetMessage.getByRole('button',{name:'Copy message as Markdown'});
 await copyButton.hover();await expect(page.getByRole('tooltip')).toHaveText('Copy as Markdown',{timeout:500});
 await expect(copyButton).not.toHaveAttribute('title');
 assert.ok(await copyButton.getAttribute('aria-describedby'));
 assert.equal(await page.getByRole('tooltip').evaluate(node=>{const probe=document.createElement('span');probe.style.background='var(--a-surface)';node.parentElement.append(probe);const expected=getComputedStyle(probe).backgroundColor;probe.remove();return getComputedStyle(node).backgroundColor===expected}),true);
 await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0);await expect(copyButton).not.toHaveAttribute('title');
 await copyButton.focus();await expect(page.getByRole('tooltip')).toHaveText('Copy as Markdown');
 await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0);await expect(copyButton).not.toHaveAttribute('title');
 await targetMessage.locator('time').hover();await expect(page.getByRole('tooltip')).toHaveCount(0);
 assert.match(await targetMessage.locator('time').getAttribute('datetime'),/^1970-/);
 await page.evaluate(()=>document.querySelector('#amp-one').dataset.interfaceDetail='detailed');
 await page.mouse.move(0,0);await page.waitForTimeout(200);
 assert.equal(await targetMessage.locator('.a-message-actions').evaluate(n=>getComputedStyle(n).opacity),'1');
 await page.evaluate(()=>document.querySelector('#amp-one').dataset.interfaceDetail='standard');
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await atBottom();
 const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 try{await page.evaluate(sessionId=>{
  const pane=document.querySelector('.a-messages'),rows=[],marks=[],gaps=[],counts={};
  const limits={rowsPerSegment:40,captureAttemptsPerSegment:80,marks:32,frameCallbacksPerSegment:40,gaps:4};
  const rowCounts={setup:0,send:0,growth:0},frameCalls={setup:0,send:0,growth:0},frameSkips={setup:0,send:0,growth:0};
  const captureAttempts={setup:0,send:0,growth:0},captureSkips={setup:0,send:0,growth:0};
  let segment='setup',inputId=null,canonicalId=null,active=true,busy=false,last='',sequence=0,dropped=0,markDrops=0;
  const originalFrame=window.requestAnimationFrame,frameDescriptor=Object.getOwnPropertyDescriptor(window,'requestAnimationFrame');
  const removals=[];let observer,resize,unsubscribe;
  const gap=error=>{if(gaps.length<limits.gaps)gaps.push(error.name)};
  const region=node=>node===pane?'pane':pane?.contains(node)?'transcript':node?.closest?.('.a-composer')?'composer':'other';
  const rect=node=>{if(!node)return null;const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,width:r.width}};
  const capture=(reason,event=null,mark=false,terminal=false)=>{
   if(!active||busy)return;busy=true;
   try{
    counts[reason]=(counts[reason]||0)+1;
    // Bound measurement work before state/DOM reads, including duplicate rows.
    // Milestones have their own budget with one slot reserved for final failure.
    if(mark){
     if(marks.length>=limits.marks-(terminal?0:1)){markDrops++;return}
    }else{
     if(captureAttempts[segment]>=limits.captureAttemptsPerSegment){captureSkips[segment]++;return}
     captureAttempts[segment]++;
    }
    const state=window.amplifier.getState(),session=state.sessions?.find(row=>row.id===sessionId);
    const messages=inputId?(session?.messages||[]).filter(row=>row.inputId===inputId):[];
    const canonical=messages.filter(row=>row.role==='user'&&row.id!==inputId);
    canonicalId=canonical.length===1?canonical[0].id:null;
    const nodes=[...pane.querySelectorAll('[data-message-id]')],users=[...pane.querySelectorAll('.a-user')];
    const targets=inputId?nodes.filter(node=>node.dataset.inputId===inputId||node.dataset.messageId===inputId):[];
    const owned=canonicalId?nodes.filter(node=>node.dataset.messageId===canonicalId&&node.dataset.inputId===inputId):[];
    const paneRect=rect(pane),top=pane.scrollTop,max=Math.max(0,pane.scrollHeight-pane.clientHeight);
    const measure=node=>node?{messageId:node.dataset.messageId,inputId:node.dataset.inputId,rect:rect(node),
     anchorError:node.getBoundingClientRect().bottom-paneRect.top-16,
     requestedTop:Math.max(0,top+node.getBoundingClientRect().bottom-paneRect.top-16)}:null;
    const snapshot={sessionId,selectedSessionId:state.selectedSessionId,paneSessionId:pane.dataset.sessionId,inputId,canonicalId,
     pane:paneRect,composer:rect(document.querySelector('.a-composer')),editor:rect(document.querySelector('.a-composer [role="textbox"]')),
     scroll:{top,height:pane.scrollHeight,client:pane.clientHeight,max,remaining:max-top},
     state:{status:session?.status,historyOffset:session?.sharedHistoryOffset,historyLoading:session?.historyLoading,
      windowMode:session?.messageWindow?.mode,workStatus:session?.workProjection?.status,navigationPending:!!state.navigationPending},
     canonicalCount:canonical.length,messages:messages.slice(0,3).map(row=>({id:row.id,inputId:row.inputId,role:row.role,delivery:row.delivery?.status})),
     targetCount:targets.length,ownedCount:owned.length,lastIsOwned:!!users.at(-1)&&users.at(-1)===owned[0],
     targetIsOwned:!!targets[0]&&targets[0]===owned[0],canonical:measure(owned[0]),controllerTarget:measure(targets[0]),
     lastUser:measure(users.at(-1)),focusedRegion:region(document.activeElement)};
    const signature=JSON.stringify(snapshot);
    if(!mark&&!event&&signature===last)return;last=signature;
    const record={sequence:++sequence,at:performance.now(),segment,reason,event,snapshot};
    if(mark){if(marks.length<limits.marks)marks.push(record);else markDrops++}
    else if(rowCounts[segment]<limits.rowsPerSegment){rows.push(record);rowCounts[segment]++}else dropped++;
   }catch(error){gap(error)}finally{busy=false}
  };
  const restore=()=>{
   for(const cleanup of [()=>observer?.disconnect(),()=>resize?.disconnect(),()=>unsubscribe?.(),...removals,()=>{
    if(window.requestAnimationFrame===observedFrame){
     if(frameDescriptor)Object.defineProperty(window,'requestAnimationFrame',frameDescriptor);else delete window.requestAnimationFrame;
    }else gap({name:'FrameWrapperReplaced'});
   }])try{cleanup()}catch(error){gap(error)}
  };
  function observedFrame(callback,...args){
   if(typeof callback!=='function')return Reflect.apply(originalFrame,this,[callback,...args]);
   return Reflect.apply(originalFrame,this,[function(...values){
    const inspect=active&&frameCalls[segment]++<limits.frameCallbacksPerSegment;
    if(active&&!inspect)frameSkips[segment]++;
    if(inspect)capture('raf-before');
    try{return Reflect.apply(callback,this,values)}
    finally{if(inspect)capture('raf-after')}
   },...args]);
  }
  try{
   if(!pane)throw Error('MissingPane');
   window.requestAnimationFrame=observedFrame;
   observer=new MutationObserver(()=>capture('dom-commit'));
   observer.observe(document.querySelector('.a-conversation')||pane,{subtree:true,childList:true,characterData:true,attributes:true});
   resize=new ResizeObserver(()=>capture('resize'));resize.observe(pane);
   const composer=document.querySelector('.a-composer');if(composer)resize.observe(composer);
   for(const type of ['scroll','wheel','touchstart','touchmove','keydown','focusin','focusout','click']){
    const target=type==='scroll'?pane:document;
    const handler=event=>capture(type,{trusted:event.isTrusted,region:region(event.target),
     ...(type==='wheel'?{deltaY:Math.sign(event.deltaY),deltaX:Math.sign(event.deltaX)}:{}),
     ...(type==='keydown'?{navigationKey:['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)}:{})});
    target.addEventListener(type,handler,{capture:true,passive:true});
    removals.push(()=>target.removeEventListener(type,handler,{capture:true}));
   }
   unsubscribe=window.amplifier.subscribe(()=>capture('state'));
   window.scrollFlowEvidence={mark(phase){
     if(phase==='before-send-click'){segment='send';last=''}
     if(phase==='before-three-grow'){segment='growth';last=''}
     capture(phase,null,true);
    },bind(id){inputId=id;capture('request-bound',null,true)},
    finish(outcome){capture('finished-'+outcome,null,true,true);active=false;
     try{restore()}catch(error){gap(error)}
     delete window.scrollFlowEvidence;
     return {outcome,limits,counts,rows,marks,dropped,markDrops,captureAttempts,captureSkips,frameCalls,frameSkips,gaps,
      controllerIntent:'unobserved: following/submitted/ready/restore are private; targets are source-rule candidates only',
      timing:'synchronous read-only observation adds overhead; only callbacks actually requested by the page are wrapped'};
    }};
   capture('before-fill',null,true);
  }catch(error){active=false;try{restore()}catch(restoreError){gap(restoreError)}throw error}
 },sessionId)}catch(error){try{console.error('SCROLL-FLOW-EVIDENCE-GAP','arm',error.name)}catch{}}
 await input.fill(Array.from({length:7},(_,i)=>`New multiline message ${i+1}`).join('\n'));
 await markScrollFlow('after-fill');
 assert.ok((await input.boundingBox()).height>=175);
 await scrollBack();
 await markScrollFlow('after-scrollback');
 // Streamed canonical acceptance must precede the delayed HTTP receipt.
 let submittedInputId;
 let settleSend;const sendSettled=new Promise(resolve=>{settleSend=resolve});
 await page.route('**/api/actions',async route=>{
  if(route.request().postDataJSON()?.action!=='conversation.send')return route.continue();
  const body=route.request().postDataJSON(),inputId=body.id;
  sendTrace.push({phase:'intercepted',inputId,sessionId:body.args.sessionId});
  heldSendRoutes.set(route,inputId);
  try{
   assert.equal(submittedInputId,undefined,'One conversation.send request, without replay');
   submittedInputId=inputId;assert.equal(body.args.sessionId,sessionId);
   try{await page.evaluate(({inputId,sessionId})=>{
    window.scrollFlowEvidence?.bind(inputId);
    const rows=[],gaps=[];let last='';
    const sample=()=>{
     try{
     const state=window.amplifier.getState(),session=state.sessions.find(s=>s.id===sessionId);
     const canonical=(session?.messages||[]).filter(m=>m.inputId===inputId&&!m.optimistic);
     const row={inputId,sessionId,messages:canonical.map(m=>({id:m.id,inputId:m.inputId,role:m.role,delivery:m.delivery?.status,
      mounted:!!document.querySelector(`[data-message-id="${CSS.escape(m.id)}"]`)})),status:session?.status};
     const key=JSON.stringify(row);if(key!==last&&rows.length<24){last=key;rows.push(row)}
     }catch(error){if(gaps.length<4)gaps.push(error.name)}
    };
    const unsubscribe=window.amplifier.subscribe(sample);sample();
    window.scrollAckEvidence={rows,gaps,sample,unsubscribe};
   },{inputId,sessionId:body.args.sessionId})}
   catch(error){sendTrace.push({phase:'observer-gap',inputId,errorClass:error.name})}
   const response=await route.fetch();
   await markScrollFlow('upstream-fetched');
   const receipt=await response.json();
   sendTrace.push({phase:'fetched',inputId,httpStatus:response.status(),accepted:receipt.accepted,delivery:receipt.delivery,
    receiptInputId:receipt.inputId,receiptSessionId:receipt.sessionId});
   assert.equal(response.status(),200);assert.equal(receipt.accepted,true);assert.equal(receipt.delivery,'accepted');
   assert.equal(receipt.inputId,inputId);assert.equal(receipt.sessionId,sessionId);
   await new Promise(resolve=>setTimeout(resolve,450));
   const beforeReceipt=await page.evaluate(({inputId,sessionId})=>{
    window.scrollFlowEvidence?.mark('before-http-release');
    const state=window.amplifier.getState(),session=state.sessions.find(s=>s.id===sessionId);
    return {status:session?.status,messages:(session?.messages||[]).filter(m=>m.inputId===inputId).map(m=>({
     id:m.id,inputId:m.inputId,role:m.role,delivery:m.delivery?.status,
     mounted:!!document.querySelector(`.a-user[data-message-id="${CSS.escape(m.id)}"][data-input-id="${CSS.escape(inputId)}"]`)}))};
   },{inputId,sessionId});
   sendTrace.push({phase:'canonical-before-http-release',inputId,...beforeReceipt});
   assert.equal(acceptedCanonical(beforeReceipt.messages,inputId),true,'Canonical accepted input precedes HTTP receipt');
   assert.equal(beforeReceipt.messages[0].mounted,true);assert.equal(beforeReceipt.status,'working');
   const canonical=beforeReceipt.messages[0];
   assert.equal(acceptedCanonical([{...canonical,delivery:'sending'}],inputId),false,'Sending is not acknowledged');
   assert.equal(acceptedCanonical([{...canonical,id:inputId}],inputId),false,'Optimistic row is not canonical');
   assert.equal(acceptedCanonical([canonical],'another-input'),false,'Wrong input cannot qualify');
   assert.equal(acceptedCanonical([canonical,canonical],inputId),false,'Duplicate canonical rows cannot qualify');
   await route.fulfill({response});heldSendRoutes.delete(route);
   await markScrollFlow('http-released');
   sendTrace.push({phase:'released',inputId});settleSend({inputId,canonicalId:canonical.id});
  }catch(error){
   sendTrace.push({phase:'send-error',inputId,errorClass:error.name});
   // Unrouting an unsettled mutation can forward it. Abort, never continue.
   await abortHeldSend(route);
   settleSend({error});
  }
 });
 await markScrollFlow('before-send-click');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await markScrollFlow('after-send-click');
 await page.waitForFunction(()=>document.querySelector('.a-user:last-of-type')?.textContent.includes('New multiline message')||[...document.querySelectorAll('.a-user')].at(-1)?.textContent.includes('New multiline message'));
 await expect.poll(()=>readComposerDraft(input)).toBe('');
 await markScrollFlow('draft-cleared-before-bottom');
 await atBottom();
 await markScrollFlow('bottom-passed');
 const sent=await sendSettled;if(sent.error)throw sent.error;
 assert.equal(sendTrace.filter(row=>row.phase==='intercepted').length,1);
 assert.equal(fixtureAcks.filter(row=>row.phase==='runtime-entered'&&row.inputId===sent.inputId).length,1);
 await expect.poll(()=>fixtureAcks.filter(row=>row.phase==='request-returned'&&row.inputId===sent.inputId).length).toBe(1);
 assert.equal(fixtureAcks.filter(row=>row.phase==='ack-returning'&&row.inputId===sent.inputId).length,1);
 assert.equal(sendTrace.some(row=>row.phase==='observer-gap'),false);
 // The streamed canonical row stays visible through HTTP receipt delivery.
 await expect.poll(()=>page.evaluate(({inputId,canonicalId})=>{
  const rows=[...document.querySelectorAll(`.a-user[data-input-id="${CSS.escape(inputId)}"]`)];
  if(rows.length!==1||rows[0].dataset.messageId!==canonicalId)return false;
  const row=rows[0];
  const message=row.getBoundingClientRect(),composer=document.querySelector('.a-composer').getBoundingClientRect(),pane=document.querySelector('.a-messages').getBoundingClientRect();
  return message.bottom<=composer.top&&message.top>=pane.top;
 },{inputId:sent.inputId,canonicalId:sent.canonicalId})).toBe(true);
 await markScrollFlow('canonical-geometry-passed');

 // The response may grow, but the start stays visible once there is enough
 // content to position the submitted message near the top of the viewport.
 const streamingChunks=async()=>((await pane.innerText()).match(/More streaming content\./g)||[]).length;
 const grow=async()=>{const chunks=await streamingChunks();await page.evaluate(()=>fetch('/api/fixture/grow',{method:'POST'}));await expect.poll(streamingChunks).toBe(chunks+25)};
 await markScrollFlow('before-three-grow');
 await grow();await markScrollFlow('after-grow-one');
 await grow();await markScrollFlow('after-grow-two');
 await grow();await markScrollFlow('after-grow-three');await page.waitForTimeout(300);
 await markScrollFlow('before-anchor');
 let replyAnchorPassed=false;
 try{
  await page.waitForFunction(()=>{const pane=document.querySelector('.a-messages'),user=[...pane.querySelectorAll('.a-user')].at(-1);return Math.abs(user.getBoundingClientRect().bottom-pane.getBoundingClientRect().top-16)<3});
  replyAnchorPassed=true;
 }finally{
  // One read-only post-assertion snapshot, not another positioning attempt or a
  // reconstruction of the last poll. Diagnostic failure cannot mask the oracle.
  try{
   const geometry=await page.evaluate(({inputId,canonicalId})=>{
    const panes=[...document.querySelectorAll('.a-messages')],pane=panes[0];
    if(!pane)return {paneCount:0};
    const rect=pane.getBoundingClientRect(),top=pane.scrollTop,max=Math.max(0,pane.scrollHeight-pane.clientHeight);
    const rows=[...pane.querySelectorAll('[data-message-id]')],users=[...pane.querySelectorAll('.a-user')];
    const canonical=rows.filter(node=>node.dataset.messageId===canonicalId&&node.dataset.inputId===inputId);
    const candidates=rows.filter(node=>node.dataset.inputId===inputId||node.dataset.messageId===inputId),last=users.at(-1);
    const measure=node=>{
     if(!node)return null;const r=node.getBoundingClientRect();
     return {messageId:node.dataset.messageId,inputId:node.dataset.inputId,top:r.top,bottom:r.bottom,
      error:r.bottom-rect.top-16,requestedTop:Math.max(0,top+r.bottom-rect.top-16)};
    };
    return {inputId,canonicalId,paneCount:panes.length,paneSessionId:pane.dataset.sessionId,
     selectedSessionId:window.amplifier.getState().selectedSessionId,paneTop:rect.top,paneBottom:rect.bottom,
     scrollTop:top,scrollHeight:pane.scrollHeight,clientHeight:pane.clientHeight,maxScrollTop:max,remaining:max-top,
     canonicalCount:canonical.length,controllerCandidateCount:candidates.length,
     lastIsCanonical:!!last&&last===canonical[0],controllerIsCanonical:!!candidates[0]&&candidates[0]===canonical[0],
     canonical:measure(canonical[0]),controllerCandidate:measure(candidates[0]),assertionNode:measure(last)};
   },{inputId:sent.inputId,canonicalId:sent.canonicalId});
   console.log('SCROLL-REPLY-ANCHOR-BOUNDARY',JSON.stringify({passed:replyAnchorPassed,geometry}));
  }catch(error){console.error('SCROLL-REPLY-ANCHOR-EVIDENCE-GAP',error.name)}
 }
 await finishScrollFlow('anchor-passed');
 const replyStart=await readingPosition();
 await grow();await page.waitForTimeout(300);
 await checkReading(replyStart,'streaming should stop following at the submitted message');
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await atBottom();
 await grow();await page.waitForTimeout(300);
 assert.ok(await pane.evaluate(element=>element.scrollHeight-element.scrollTop-element.clientHeight)>80,'jumping is a one-time action, not continuous follow: '+JSON.stringify(await pane.evaluate(element=>({top:element.scrollTop,height:element.scrollHeight,viewport:element.clientHeight}))));
 await scrollBack();
 const savedPosition=await readingPosition();
 await grow();await page.waitForTimeout(200);
 await checkReading(savedPosition,'streaming must preserve scrollback');
 // Retain the original raw-position invariant at a stable Work boundary.
 await workReady();
 // A routine Work refresh must not insert any loading row above live text,
 // even for one frame. Observe actual React mutations before paint.
 await pane.evaluate(element=>{
  const top=element.getBoundingClientRect().top,rows=[...element.querySelectorAll('[data-message-id]')];
  const anchor=rows.find(row=>row.getBoundingClientRect().bottom>top)||rows.at(-1);
  const offset=anchor.getBoundingClientRect().top-top;
  window.workNoticeFrames=[];
  window.workNoticeObserver=new MutationObserver(()=>{
   if(window.workNoticeFrames.length<100)window.workNoticeFrames.push({notice:!!element.querySelector('[data-part="work-projection-status"]'),expected:offset,actual:anchor.getBoundingClientRect().top-element.getBoundingClientRect().top});
  });
  window.workNoticeObserver.observe(element,{subtree:true,childList:true,attributes:true,attributeFilter:['data-work-projection']});
 });
 const stableBefore=await readingPosition();
 await grow();await workReady();const stableAfter=await readingPosition();
 assert.ok(Math.abs(stableAfter.contentTop-stableBefore.contentTop)<3,'Settled Work preserves the anchor content position');
 assertReading(stableBefore,stableAfter,'streaming with stable geometry preserves raw scrollback');
 readingChecks.push({message:'stable Work geometry',before:stableBefore,after:stableAfter});
 const noticeFrames=await page.evaluate(()=>{window.workNoticeObserver.disconnect();return window.workNoticeFrames});
 assert.ok(noticeFrames.length>0,'Observe actual streaming or Work projection mutations');
 for(const frame of noticeFrames){
  assert.equal(frame.notice,false,'Live Work refresh must not insert a history-loading row');
  assert.ok(Math.abs(frame.actual-frame.expected)<3,'Work refresh preserves reading position before paint: '+JSON.stringify(frame));
 }
 console.log('Quiet Work refresh pre-paint positions:',JSON.stringify(noticeFrames));
 // Counterchecks exercise real scroll changes without a compensating layout
 // change: an 80px reading shift and a jump to latest must both be rejected.
 const beforeShift=await readingPosition();
 await pane.evaluate(element=>element.scrollTop+=80);
 const afterShift=await readingPosition();
 assert.throws(()=>assertReading(beforeShift,afterShift,'deliberate reading shift'),/deliberate reading shift/);
 readingChecks.push({message:'rejected real 80px reading shift',before:beforeShift,after:afterShift});
 await pane.evaluate((element,top)=>element.scrollTop=top,beforeShift.scrollTop);
 assertReading(beforeShift,await readingPosition(),'restore after reading-shift countercheck');
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await atBottom();
 const afterJump=await readingPosition();
 assert.throws(()=>assertReading(beforeShift,afterJump,'deliberate latest jump'),/deliberate latest jump/);
 readingChecks.push({message:'rejected real jump to latest',before:beforeShift,after:afterJump});
 await pane.evaluate((element,top)=>element.scrollTop=top,beforeShift.scrollTop);
 assertReading(beforeShift,await readingPosition(),'restore after latest-jump countercheck');
 // A stream-only movement must also fail when the identified message and
 // raw pane position remain unchanged; neither can substitute for visibility.
 const stream=pane.locator(':scope > .a-assistant:not([data-message-id])');
 const transform=await stream.evaluate(element=>element.style.transform);
 await stream.evaluate(element=>element.style.transform='translateY(80px)');
 const shiftedStream=await readingPosition();
 assert.equal(shiftedStream.id,beforeShift.id);assert.equal(shiftedStream.offset,beforeShift.offset);
 assert.equal(shiftedStream.scrollTop,beforeShift.scrollTop);
 assert.throws(()=>assertReading(beforeShift,shiftedStream,'deliberate stream-only shift'),/\(stream paragraph\)|\(visible stream offset\)/);
 readingChecks.push({message:'rejected stream-only shift with unchanged message/pane',before:beforeShift,after:shiftedStream});
 await stream.evaluate((element,value)=>element.style.transform=value,transform);
 assertReading(beforeShift,await readingPosition(),'restore after stream-only countercheck');
 await input.fill('Keep this draft while I check another chat');
 await page.waitForTimeout(400);
 const anchorBefore=await pane.evaluate(element=>{const top=element.getBoundingClientRect().top;const rows=[...element.querySelectorAll('[data-message-id]')];const node=rows.find(n=>n.getBoundingClientRect().bottom>top)||rows.at(-1);return {id:node.dataset.messageId,offset:node.getBoundingClientRect().top-top}});
 await action('session.create',{title:'Another chat'});await action('session.select',{id:sessionId});
 await page.waitForTimeout(500);
 assert.equal(await readComposerDraft(input),'Keep this draft while I check another chat');
 const offset=await pane.evaluate((element,id)=>[...element.querySelectorAll('[data-message-id]')].find(n=>n.dataset.messageId===id).getBoundingClientRect().top-element.getBoundingClientRect().top,anchorBefore.id);
 assert.ok(Math.abs(offset-anchorBefore.offset)<3,`chat switch restores reading anchor: ${offset} vs ${anchorBefore.offset}`);
 await page.reload();await page.waitForSelector('#amp-one');await page.waitForTimeout(500);
 const afterReload=await pane.evaluate((element,id)=>[...element.querySelectorAll('[data-message-id]')].find(n=>n.dataset.messageId===id).getBoundingClientRect().top-element.getBoundingClientRect().top,anchorBefore.id);
 assert.ok(Math.abs(afterReload-anchorBefore.offset)<3,'tab reload restores reading position');
 assert.equal(await page.locator('.a-rail-mark').nth(3).getAttribute('data-bookmarked'),'true');
 await page.locator('.a-rail-mark').nth(3).hover();
 await page.screenshot({path:process.env.AMPLIFIER_READING_SCREENSHOT||'/tmp/unified-reading-preview.png'});
 // Returning without a state change must not fetch another navigation snapshot.
 await page.waitForTimeout(700);const shellRequests=requests.filter(path=>path==='/api/shell').length;
 await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
 await page.waitForTimeout(350);assert.equal(requests.filter(path=>path==='/api/shell').length,shellRequests);
 // Hidden documents process live state but defer rendering until visible.
 const messageText=()=>pane.locator('.a-detail-text').allTextContents();
 const rendered=await messageText();
 await page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,value:true}));
 await page.request.post(new URL('/api/fixture/hidden-reply',page.url()).href);
 await page.waitForFunction(()=>window.amplifier.getState().sessions.some(s=>s.messages.some(m=>m.text==='Fresh reply received while this tab was hidden.')));
 assert.deepEqual(await messageText(),rendered);
 await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});
 await expect.poll(messageText).not.toEqual(rendered);
 assert.deepEqual(errors,[]);
 console.log('Streaming reading positions:',JSON.stringify(readingChecks));
 console.log('Chat navigator tooltip removal, visible turns, preview placement/resize and scroll browser checks passed: delayed send receipt, reply start anchoring, jump button, streaming scrollback, draft preservation and reading position on chat switch/reload.');
}catch(error){primaryFailure=error;throw error}
finally{
 let cleanupFailure,heldRoutesSettled=false;
 try{
  await finishFixture({label:'Scroll fixture',close:[
   ['scroll-flow evidence',()=>finishScrollFlow(primaryFailure?'failed':'scenario-passed')],
   ['held-send aborts',async()=>{
    for(const route of [...heldSendRoutes.keys()])await abortHeldSend(route);
    heldRoutesSettled=true;
   }],
   ['scroll-send ack evidence',async()=>{
    try{
     const sse=await page?.evaluate(()=>{const evidence=window.scrollAckEvidence;if(!evidence)return null;
      evidence.sample();evidence.unsubscribe();delete window.scrollAckEvidence;return {rows:evidence.rows,gaps:evidence.gaps}});
     console.log('SCROLL-SEND-ACK-EVIDENCE',JSON.stringify({requests:sendTrace,fixture:fixtureAcks,transports,stream:sse}));
    }catch(error){console.error('SCROLL-SEND-ACK-EVIDENCE-GAP',error.name)}
   }],
   // An unsuccessful or still-pending abort is not permission to forward a request.
   ['unroute',()=>{if(!routeAbortFailed&&heldRoutesSettled)return page?.unrouteAll({behavior:'ignoreErrors'})}],
   ['browser-close',()=>browser?.close()],
   ['vite-close',()=>vite?.close()],
  ],stop:stopFixture});
 }catch(error){cleanupFailure=error;console.error(error)}
 try{
  console.log('SCROLL-CLEANUP',JSON.stringify({routeAbortFailed,heldRoutesSettled,fixture:fixtureReceipts,
   errors:(cleanupFailure?.errors||[]).map(error=>({errorClass:error.name,message:error.message}))}));
  if(primaryFailure||routeAbortFailed||cleanupFailure)console.error('SCROLL-FIXTURE-STDERR',fixtureLog);
 }catch{}
 if(!primaryFailure){
  if(routeAbortFailed)throw Error('Held send route abort was not confirmed');
  if(cleanupFailure)throw cleanupFailure;
 }
}
