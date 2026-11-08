import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {startCoordinationFixture} from './coordination-fixture.mjs';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python';
const outputBase=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(outputBase,{recursive:true});
const outputDir=await mkdtemp(join(outputBase,'request-cancellation-browser-'));
const fixture=await startCoordinationFixture({root,python,output:outputDir});
let browser,page,failure,result;
try{
 const url=fixture.base;
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);
 const errors=[],actions=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 await page.goto(url);await showFixtureConversation(page);
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const current=await inspect(),composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep my unsent guidance');
 const send=id=>page.evaluate(({sender,target,id})=>window.amplifier.dispatch('coordination.send',{
  senderSessionId:sender,sessionId:target,mode:'queue',text:'Scoped check '+id},{id}),{sender:current.selected,target:current.other,id});
 await send('active-request');
 await expect.poll(async()=>(await inspect()).sent.filter(row=>row.inputId==='active-request').length).toBe(1);
 await send('queued-request');await send('keep-request');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 await page.getByText('Exact requests, messages and saved results',{exact:true}).click();
 const queued=page.locator('[data-request-id="queued-request"]');
 await queued.getByText('Withdraw this request',{exact:true}).click();
 await queued.getByLabel('Optional guidance').fill('No longer needed; keep the other checks.');
 await queued.getByRole('button',{name:'Withdraw request',exact:true}).click();
 await expect(queued).toContainText('Withdrawn before execution');
 let state=await inspect();
 assert.equal(state.coordination.requests.find(row=>row.requestId==='queued-request').delivery,'cancelled');
 assert.equal(state.coordination.requests.find(row=>row.requestId==='keep-request').delivery,'queued');
 assert.equal(state.stops.length,0);
 const active=page.locator('[data-request-id="active-request"]');
 await active.getByText('Ask to stop this request',{exact:true}).click();
 await active.getByLabel('Optional guidance').fill('Preserve completed results.');
 let lost=false;
 await page.route('**/api/actions',async route=>{
  if(!lost&&route.request().postDataJSON()?.action==='coordination.cancel'){
   lost=true;await route.fetch();await route.abort('connectionreset');
  }else await route.continue();
 });
 await active.getByRole('button',{name:'Request cancellation',exact:true}).click();
 await expect(active.getByRole('button',{name:'Check cancellation again',exact:true})).toBeVisible();
 await active.getByRole('button',{name:'Check cancellation again',exact:true}).click();
 await expect(active).toContainText('stopped execution is not yet confirmed');
 const cancellations=actions.filter(row=>row.action==='coordination.cancel'&&row.args.requestId==='active-request');
 assert.equal(cancellations.length,2);assert.equal(cancellations[0].id,cancellations[1].id);
 state=await inspect();
 assert.equal(state.stops.length,0);assert.equal(state.statuses[current.other],'working');
 assert.equal(state.sent.filter(row=>row.inputId==='active-request').length,1);
 assert.equal(state.sent.filter(row=>row.inputId==='queued-request').length,0);
 assert.equal(state.coordination.requests.find(row=>row.requestId==='active-request').cancellation.reason,'Preserve completed results.');
 await page.screenshot({path:join(outputDir,'desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({path:join(outputDir,'mobile.png'),animations:'disabled'});
 // Rendering contract for canonical rejected/restarted reports. Their real
 // service transitions are covered in test_request_cancellation.py; here the
 // response fixture ensures the UI does not replace unconfirmed with waiting.
 for(const reportStatus of ['rejected','unknown']){
  let reportDelivered=false;
  const unconfirmed=async route=>{
   if(route.request().postDataJSON()?.action!=='coordination.context')return route.fallback();
   const response=await route.fetch(),body=await response.json();
   const row=body.result.requests.find(row=>row.requestId==='active-request');
   row.response={kind:'result',outcome:'cancelled',status:reportStatus,qualified:false};
   row.cancellation={...row.cancellation,status:'reported_unconfirmed',detail:'A cancellation report was saved, but completion could not be confirmed. Inspect the retained result before deciding what to do next.'};
   await route.fulfill({response,json:body});
   reportDelivered=true;
  };
  await page.route('**/api/actions',unconfirmed);
  await page.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
  // Both reports use the same text. Wait for this response, not a matching
  // render left behind by the preceding report.
  await expect.poll(()=>reportDelivered).toBe(true);
  await expect(page.getByRole('region',{name:'Workspace work overview'})).not.toHaveAttribute('inert');
  await expect(active.locator('[data-part="request-cancellation"]')).toContainText('completion could not be confirmed');
  await expect(active.locator('[data-part="request-cancellation"]')).not.toContainText('waiting');
  await page.unroute('**/api/actions',unconfirmed);
 }
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveDraft('Keep my unsent guidance');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 assert.deepEqual(errors,[]);
 result={queuedWithdrawn:true,otherRequestRetained:true,activeCancellationUnconfirmed:true,lostAcknowledgementSameIdentity:true,noWholeChatStop:true,noReplay:true,draftAndSelectionPreserved:true,mobileNoOverflow:true,rejectedAndUnknownRendering:true,reportRenderingUsesResponseFixtures:true,scriptedRuntime:true,liveModelEvidence:false,outputDir};
}catch(error){failure=error;await page?.screenshot({path:join(outputDir,'failure.png')}).catch(()=>{});throw error}
finally{await fixture.finish({close:[['Page routes',()=>page?.unrouteAll({behavior:'wait'})],['Browser',()=>browser?.close()]],originalError:failure})}
await writeFile(join(outputDir,'result.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result));
