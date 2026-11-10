// Built client and synthetic server: malformed SSE events retain app recovery ownership.
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {startChatFixture} from './chat-fixture.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const {base,stop}=await startChatFixture();
let browser,failure;
try{
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/clients/attach',async route=>{
  const response=await route.fetch(),attachment=await response.json();
  await route.fulfill({response,json:{...attachment,transports:['sse']}});
 });
 await page.addInitScript(()=>{
  const Native=window.EventSource;window.streamProbe={sources:[],shell:[]};
  window.EventSource=class extends Native{
   constructor(...args){super(...args);this.frames={state:0,'state-delta':0,shell:0};this.closes=0;streamProbe.sources.push(this);
    for(const event of Object.keys(this.frames))this.addEventListener(event,()=>this.frames[event]++);
   }
   close(){this.closes++;super.close()}
  };
  window.addEventListener('amplifier-shell',event=>streamProbe.shell.push(event.detail));
 });
 await page.goto(base);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const identity=await page.evaluate(()=>window.amplifier.getState().client.id);
 const readHistory=async()=>{const response=await context.request.get(base+'/fixture/message-source');assert.equal(response.status(),200);return response.json()};
 const before=await readHistory();
 const built=await page.request.get(base+'/build.json').then(response=>response.json());
 assert.equal(await page.evaluate(()=>window.amplifier.getState().client.build.frontend.id),built.id);
 const initial=await page.evaluate(()=>({sources:streamProbe.sources.length,baselines:streamProbe.sources[0].frames.state}));
 assert.equal(initial.sources,1);assert.ok(initial.baselines>0);

 // External accepted view updates can reach this page only through its stream.
 const changeWidth=async width=>{
  const count=await page.evaluate(()=>streamProbe.sources.at(-1).frames['state-delta']);
  const response=await context.request.post(base+'/api/actions',{headers:{'X-Amplifier-Client':identity},data:{action:'view.update',args:{patch:{navWidth:width}}}});
  assert.equal(response.status(),200);assert.equal((await response.json()).accepted,true);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.navWidth)).toBe(width);
  await expect.poll(()=>page.evaluate(()=>streamProbe.sources.at(-1).frames['state-delta'])).toBeGreaterThan(count);
 };
 await changeWidth(417);
 const ignored=await page.evaluate(()=>{
  const source=streamProbe.sources[0],revision=window.amplifier.getState().revision,shell=streamProbe.shell.length;
  for(const event of ['state','shell'])source.dispatchEvent(new MessageEvent(event,{data:'not json'}));
  return {revisionUnchanged:revision===window.amplifier.getState().revision,shellUnchanged:shell===streamProbe.shell.length,closes:source.closes};
 });
 assert.deepEqual(ignored,{revisionUnchanged:true,shellUnchanged:true,closes:0});
 await changeWidth(419);
 assert.equal(await page.evaluate(()=>streamProbe.sources.length),1,'A malformed snapshot must retain the usable delta baseline');

 const closed=await page.evaluate(()=>{
  const source=streamProbe.sources[0];source.dispatchEvent(new MessageEvent('state-delta',{data:'not json'}));
  return {closes:source.closes,readyState:source.readyState};
 });
 assert.deepEqual(closed,{closes:1,readyState:2});
 await expect.poll(()=>page.evaluate(()=>streamProbe.sources.length)).toBe(2);
 await expect.poll(()=>page.evaluate(()=>streamProbe.sources[1].frames.state)).toBeGreaterThan(0);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.navWidth)).toBe(419);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().client.id),identity);

 const late=await page.evaluate(()=>{
  // Recovery and the retired React effect can both close this source. Late
  // callbacks must add no close/recovery work after that normal cleanup.
  const retired=streamProbe.sources[0],closes=retired.closes,shell=streamProbe.shell.length;
  retired.dispatchEvent(new MessageEvent('state-delta',{data:'not json'}));
  retired.dispatchEvent(new MessageEvent('shell',{data:'{"revision":999999}'}));
  retired.onopen?.(new Event('open'));retired.onerror?.(new Event('error'));
  return {closesUnchanged:closes===retired.closes,shellUnchanged:shell===streamProbe.shell.length};
 });
 assert.deepEqual(late,{closesUnchanged:true,shellUnchanged:true});
 await changeWidth(421);assert.equal(await page.evaluate(()=>streamProbe.sources.length),2);
 const after=await readHistory();assert.deepEqual(after,before,'Recovery must preserve canonical history and never submit work');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,build:built.id,transport:'sse',malformedStateIgnored:true,malformedShellIgnored:true,
  malformedDeltaRecovered:true,identityPreserved:true,lateCallbacksIgnored:true,historyPreserved:true,sends:after.sends.length}));
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'State stream fixture',close:[['State stream browser',()=>browser?.close()]],stop,originalError:failure})}
