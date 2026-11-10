import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser,release,page,primary,historyFacts;
const secondary=[],requests=[],trackedRequests=new Map();
let requestDropped=0;
const out=root+'output/browser-checks/appearance-startup';
const errorFact=error=>({name:error.name,code:error.code??null});
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const url=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const {alpha}=await control({op:'heavy',active:false,messages:160,otherMessages:1,chars:500,nodes:0});
 await control({op:'patch',sessions:{[alpha]:{completion:{id:'startup-completion',at:Date.now()/1000}}}});
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/shell?*',async route=>{await gate;await route.continue()});
 await page.goto(url,{waitUntil:'domcontentloaded'});
 await expect(page.locator('.boot')).toBeVisible();
 // State arrives while the conversation DOM is deliberately absent. Effects
 // must attach when the shell is ready even if that state never changes.
 await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 await showFixtureConversation(page);
 const read=()=>page.evaluate(id=>window.amplifier.getState().attention.items.find(item=>item.id==='completion:'+id)?.read,alpha);
 assert.equal(await read(),false);
 release();await page.unrouteAll({behavior:'wait'});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await expect(page.locator('[data-message-id]')).toHaveCount(100);
 await expect.poll(read).toBe(true);
 const pane=page.getByRole('log',{name:'Conversation messages'});
 await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBeGreaterThan(120);
 const detailReads=[];page.on('request',request=>{if(new URL(request.url()).pathname==='/api/conversation/detail')detailReads.push(request.url())});
 page.on('request',request=>{
  const url=new URL(request.url());if(url.pathname!=='/api/conversation/detail')return;
  if(requests.length>=8){requestDropped++;return}
  const row={index:requests.length,at:performance.now(),epochMs:Date.now(),clock:'node-performance',method:request.method(),
   sourceMatches:url.searchParams.get('sessionId')===alpha,
   part:['messages','execution'].includes(url.searchParams.get('part'))?url.searchParams.get('part'):'other',
   beforePresent:url.searchParams.has('before'),status:null};
  requests.push(row);trackedRequests.set(request,row);
 });
 page.on('response',response=>{
  const row=trackedRequests.get(response.request());
  if(row){row.responseAt=performance.now();row.responseEpochMs=Date.now();row.status=response.status()}
 });
 page.on('requestfailed',request=>{
  const row=trackedRequests.get(request);if(row){row.failed=true;row.failedAt=performance.now()}
 });
 // Observe the existing single gesture, without reading or changing the private
 // follower arm/busy variables. Frame probes are not awaited by the gesture.
 await pane.evaluate((el,alpha)=>{
  const rows=[],failures=[],frames=new Set();let dropped=0,sequence=0,wheels=0,frameIndex=0;
  const scalar=v=>typeof v==='string'&&v.length<=100?v:null;
  const number=v=>Number.isFinite(v)?v:null;
  const record=(phase,event)=>{
   try{
    if(rows.length>=32){dropped++;return}
    const state=window.amplifier?.getState(),session=state?.sessions?.find(s=>s.id===alpha),w=session?.messageWindow;
    rows.push({sequence:++sequence,phase,at:performance.now(),epochMs:Date.now(),clock:'browser-performance',frameIndex,
     selectedMatches:state?.selectedSessionId===alpha,paneMatches:el.dataset.sessionId===alpha,
     scrollTop:el.scrollTop,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
     distanceWithin120:el.scrollTop<=120,renderedCount:el.querySelectorAll('[data-message-id]').length,
     messagesBusy:scalar(el.getAttribute('aria-busy')),historyLoading:session?.historyLoading===true,
     window:{mode:scalar(w?.mode),offset:number(w?.offset),total:number(w?.total),
      hasEarlier:w?.hasEarlier===true,beforePresent:typeof w?.before==='string'&&w.before.length>0,
      sharedHistoryOffset:number(session?.sharedHistoryOffset)},
     ...(event?{event:{type:event.type,trusted:event.isTrusted,
      deltaY:number(event.deltaY),deltaX:number(event.deltaX),deltaMode:number(event.deltaMode),
      ctrl:event.ctrlKey===true}}:{})});
   }catch(error){if(failures.length<4)failures.push({name:error.name})}
  };
  const nextFrame=phase=>{
   const id=requestAnimationFrame(()=>{frames.delete(id);frameIndex++;record(phase)});
   frames.add(id);
  };
  const wheel=event=>{
   record('wheel-listener',event);
   if(++wheels===1){
    const id=requestAnimationFrame(()=>{
     frames.delete(id);frameIndex++;record('first-frame-after-wheel');nextFrame('second-frame-after-wheel');
    });frames.add(id);
   }
  };
  const scroll=event=>record('scroll-listener',event);
  el.addEventListener('wheel',wheel,{passive:true});el.addEventListener('scroll',scroll,{passive:true});
  window.__appearanceHistoryDiagnostic={record,finish:()=>{
   record('before-cleanup');el.removeEventListener('wheel',wheel);el.removeEventListener('scroll',scroll);
   for(const id of frames)cancelAnimationFrame(id);
   delete window.__appearanceHistoryDiagnostic;
   return {rows,failures,dropped,armState:'not observable without product instrumentation'};
  }};
  record('installed');
 },alpha);
 await pane.evaluate(el=>{
  window.__appearanceHistoryDiagnostic?.record('before-original-scroll-assignment');
  el.scrollTop=80;
  window.__appearanceHistoryDiagnostic?.record('after-original-scroll-assignment');
 });
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>{
  window.__appearanceHistoryDiagnostic?.record('original-restoration-frame-one');
  requestAnimationFrame(()=>{window.__appearanceHistoryDiagnostic?.record('original-restoration-frame-two');resolve()});
 })));
 assert.equal(detailReads.length,0,'restoration/anchoring does not load unrequested older history');
 await expect(page.locator('[data-message-id]')).toHaveCount(100);
 await pane.hover();await page.mouse.wheel(0,-60);
 await expect(page.locator('[data-message-id]')).toHaveCount(160);
 assert.equal(detailReads.length,1,'one intentional older-history gesture loads one page');
 assert.deepEqual(errors,[]);
 console.log('Delayed appearance startup attaches completion acknowledgement; only intended earlier-history scrolling loads another page.');
}catch(error){primary=error}
finally{
 if(page&&!page.isClosed()){
  let timer;
  try{
   historyFacts=await Promise.race([page.evaluate(()=>window.__appearanceHistoryDiagnostic?.finish()??{notInstalled:true}),
    new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('History diagnostic read timed out')),2000)})]);
   if(historyFacts.failures?.length)secondary.push(Error('History diagnostic callback failed'));
  }catch(error){secondary.push(error)}finally{clearTimeout(timer)}
 }
 try{
  await mkdir(out,{recursive:true});
  await writeFile(out+'/history-gesture.json',JSON.stringify({historyFacts,requests,requestDropped,
   primary:primary?errorFact(primary):null,diagnosticErrors:secondary.map(errorFact)},null,2)+'\n');
 }catch(error){secondary.push(error)}
 for(const cleanup of [()=>release?.(),()=>browser?.close(),()=>fixture.kill()])
  try{await cleanup()}catch(error){secondary.push(error)}
}
if(primary&&secondary.length)throw new AggregateError([primary,...secondary],'History primary failure with diagnostic/cleanup failures',{cause:primary});
if(primary)throw primary;
if(secondary.length)throw new AggregateError(secondary,'History diagnostic/cleanup failure');
