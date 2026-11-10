import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const url=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const r=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()};
 const {alpha,beta}=await control({op:'heavy',active:false,messages:240,otherMessages:2,chars:50,nodes:0});
 const now=Date.now()/1000;
 const turns=Array.from({length:120},(_,i)=>({id:'t'+i,anchorMessageId:`${alpha}-m-${i*2}`,startedAt:now-240+i*2,endedAt:now-239+i*2,phase:'completed'}));
 await control({op:'patch',sessions:{[alpha]:{historyLoaded:false,historyLoading:true,execution:{turns,nodes:turns.map((turn,i)=>({id:'node'+i,kind:'tool',label:'Synthetic activity '+i,turnId:turn.id,anchorMessageId:turn.anchorMessageId,phase:'completed',startedAt:turn.startedAt,endedAt:turn.endedAt}))}}}});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers});
 await page.addInitScript(()=>{
  const fetch=window.fetch;window.fixtureHistoryReads=[];
  window.fetch=(url,options)=>{if(String(url).startsWith('/api/conversation/detail?'))window.fixtureHistoryReads.push({url,signal:options?.signal});return fetch(url,options)};
 });
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await expect(page.getByText('Loading conversation…',{exact:true})).toBeVisible();
 assert.equal(await page.locator('[data-part="execution-group"]').count(),0);
 assert.equal(await page.locator('[data-message-id]').count(),0);
 assert.equal(await page.getByRole('button',{name:'Load earlier conversation',exact:true}).count(),0);
 await control({op:'patch',sessions:{[alpha]:{historyLoaded:true,historyLoading:false}}});
 await expect(page.locator('[data-message-id]').first()).toBeVisible();
 const earlier=page.getByRole('button',{name:'Load earlier conversation',exact:true});await expect(earlier).toHaveCount(1);
 assert.equal(await page.getByRole('button',{name:/Load earlier (messages|activity)/}).count(),0);
 const before=await page.locator('[data-message-id]').count();
 const gate=new Promise(resolve=>release=resolve);let activityReturned=false;
 await page.route('**/api/conversation/detail?*',async route=>{
  const part=new URL(route.request().url()).searchParams.get('part');
  if(part==='messages')await gate;
  try{const response=await route.fetch();await route.fulfill({response});if(part==='groups'||part==='nodes')activityReturned=true}catch(e){if(!page.isClosed())throw e}
 });
 await earlier.click();
 await expect(page.getByRole('button',{name:'Loading earlier conversation…',exact:true})).toBeDisabled();
 await expect.poll(()=>activityReturned).toBe(true);
 assert.equal(await page.locator('[data-message-id]').count(),before);
 release();await expect(earlier).toBeEnabled();
 assert.ok(await page.locator('[data-message-id]').count()>before);
 await page.unroute('**/api/conversation/detail?*');
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const held=new Promise(resolve=>{release=resolve});let pendingRead=false;
 await page.route('**/api/conversation/detail?*',async route=>{
  pendingRead=true;await held;
  // The old reader has gone away; a late response must not restore its window.
  await route.fulfill({json:{items:[],offset:0,turns:[],segments:[]}}).catch(()=>{});
 });
 await page.evaluate(()=>{window.fixtureHistoryReads=[]});
 await earlier.evaluate(button=>button.click());await expect.poll(()=>pendingRead).toBe(true);
 await act('session.select',{id:beta});
 await expect.poll(()=>page.evaluate(()=>window.fixtureHistoryReads.length>0&&window.fixtureHistoryReads.every(row=>row.signal?.aborted))).toBe(true);
 release();await page.unrouteAll({behavior:'wait'});
 await expect(page.locator('[data-message-id]')).toHaveCount(2);
 await act('session.select',{id:alpha});await expect(earlier).toBeEnabled();
 await act('canvas.show',{kind:'text',title:'Alpha artifact',content:'Alpha canvas contents'});
 await act('view.update',{patch:{canvasWidth:570,canvasDraft:{filter:'Alpha'}}});
 const artifact=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await act('session.select',{id:beta});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.canvasWidth),undefined);
 await act('canvas.reopen');await act('view.update',{patch:{canvasWidth:340}});await act('canvas.visibility',{open:false});
 await act('session.select',{id:alpha});
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return [s.canvas.open,s.canvas.id,s.view.canvasWidth,s.view.canvasDraft.filter]}),[true,artifact,570,'Alpha']);
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return [s.canvas.open,s.canvas.id,s.view.canvasWidth]}),[true,artifact,570]);
 await act('session.select',{id:beta});
 assert.deepEqual(await page.evaluate(()=>{const s=window.amplifier.getState();return [s.canvas.open,s.view.canvasWidth]}),[false,340]);
 // Native transcript paging uses the same control and retains the entire
 // prior window until the saved transcript and activity have been applied.
 const native=await control({op:'native-history'});
 const sid=native.state.sessions.find(row=>row.nativeIdentity==='paging-fixture').id;
 await act('session.select',{id:sid});
 const count=await page.locator('[data-message-id]').count();
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).evaluate(el=>el.click());
 await expect.poll(()=>page.locator('[data-message-id]').count()).toBeGreaterThan(count);
 await expect(page.getByRole('button',{name:'Load earlier conversation',exact:true})).toBeEnabled();
 const ids=await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(r=>r.dataset.messageId));assert.equal(new Set(ids).size,ids.length);
 await page.screenshot({path:'/tmp/unified-chat-view-state.png'});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,initialHistoryGated:true,pairedHistoryPages:true,supersededReadsAborted:true,canvasPerChat:true,reloadPreserved:true}));
}finally{release?.();await browser?.close();fixture.kill()}
