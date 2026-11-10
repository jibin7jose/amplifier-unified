import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

// Real production host and assets; synthetic data, no model/provider calls.
const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks');
await mkdir(base,{recursive:true});
const output=await mkdtemp(join(base,'canvas-close-'));
const observe=process.env.CANVAS_CLOSE_OBSERVE==='1';
const report={status:'running',observe,checks:[],screenshots:[],geometry:[],errors:[],commands:[],limits:['Synthetic runtime; Chromium desktop and emulated touch, not a physical device.','Screenshots require separate pixel review.']};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,failure,stdout='',stderr='';
fixture.stderr.on('data',chunk=>stderr+=chunk);
const capture=async name=>{const file=name+'.png';await page.mouse.move(0,899);await page.screenshot({path:join(output,file)});report.screenshots.push(file)};
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n')){try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 report.browserVersion=browser.version();
 for(const width of [1500,390,320]){
  const context=await browser.newContext({viewport:{width,height:900},hasTouch:width<500,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  page=await context.newPage();page.setDefaultTimeout(10000);
  page.on('pageerror',error=>report.errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const body=request.postDataJSON();report.commands.push(body?.action==='shell.command'?(body.args.command||body.args):body)}});
  const receipts=[];
  page.on('response',response=>{
   const request=response.request();
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
   const raw=request.postDataJSON(),command=raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
   if(['canvas.tabClose','canvas.visibility'].includes(command?.action))
    receipts.push(response.json().then(body=>({command,status:response.status(),body})));
  });
  const action=async(name,args={})=>{const result=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);assert.equal(result?.accepted,true,JSON.stringify(result));return result.result};
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  await page.goto(url);await showFixtureConversation(page,{navPinned:false});
  await action('session.create');
  await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
  await action('conversation.send',{text:'Keep this synthetic conversation history.'});
  await expect.poll(async()=>{const s=await state();return s.sessions.find(row=>row.id===s.selectedSessionId)?.status}).not.toBe('working');
  await action('view.update',{patch:{canvasWidth:800,canvasControlsExpanded:false,canvasControlsPinned:false}});
  const first=await action('canvas.show',{kind:'markdown',title:'Alpha report',content:'# Alpha original\n\nSaved original.'});
  await action('canvas.versions.revise',{id:first.id,expectedRevision:1,content:'# Alpha revised\n\nSaved revision.'});
  const second=await action('canvas.show',{kind:'markdown',title:'Beta report',content:'# Beta retained\n\nOther open document.'});
  const panel=page.locator('#workspace-canvas');
  await expect(panel.getByRole('heading',{name:'Beta retained',exact:true})).toBeVisible();
  await page.mouse.move(0,899);
  await capture((observe?'before':'after')+'-'+width);
  report.geometry.push({width,controls:await panel.locator('button').evaluateAll(nodes=>nodes.filter(el=>el.getClientRects().length).map(el=>{const r=el.getBoundingClientRect();return {label:el.getAttribute('aria-label'),text:el.textContent,role:el.getAttribute('role'),x:r.x,y:r.y,width:r.width,height:r.height}}))});
  if(observe){
   await panel.getByRole('button',{name:'Open documents, 2 open',exact:true}).click();
   await expect(page.getByRole('dialog',{name:'Open documents',exact:true})).toBeVisible();
   await capture('before-open-documents-'+width);
   await context.close();continue;
  }
  // Keep the actual DOM target addressable during the deliberate hidden-control
  // negative. Geometry, not accessibility-tree absence/timeouts, is the oracle.
  const closeTab=title=>panel.locator('.a-canvas-document-tabs').getByRole('button',{name:`Close tab ${title}`,exact:true,includeHidden:true});
  const dismiss=panel.getByRole('button',{name:'Close canvas panel',exact:true,includeHidden:true});
  const overview=panel.getByRole('tab',{name:'Chat overview',exact:true});
  const toggle=page.locator('#canvas-toggle');
  const artifactRows=async()=>{const s=await state();return s.canvasArtifacts.filter(row=>row.sessionId===s.selectedSessionId)};
  const versions=async()=>Promise.all([[first.id,1],[first.id,2],[second.id,1]].map(async([id,version])=>{
   const result=await action('canvas.versions.inspect',{id,version,includeSource:true});
   return {id,version,source:result.source,versions:result.versions,revision:result.revision};
  }));
  const savedBefore=await versions(),snapshot=await state();
  const historyBefore=snapshot.sessions.find(row=>row.id===snapshot.selectedSessionId).messages;
  const fixtureBefore=await (await page.request.get(url+'/fixture')).json();
  if(process.env.AMPLIFIER_TEST_INSTALLED==='1'){
   assert.equal(fixtureBefore.installedIdentity?.verified,true);
   (report.installedIdentities??=[]).push(fixtureBefore.installedIdentity);
  }
  const targetGeometry=locator=>locator.evaluate(el=>{
   const r=el.getBoundingClientRect(),points=[[r.x+4,r.y+4],[r.right-4,r.y+4],[r.right-4,r.bottom-4],[r.x+4,r.bottom-4],[r.x+r.width/2,r.y+r.height/2]];
   const hits=points.map(([x,y])=>{const node=document.elementFromPoint(x,y);return {x,y,inside:el.contains(node),target:node?.getAttribute('aria-label')||node?.className}});
   return {width:r.width,height:r.height,left:r.left,right:r.right,hits,
    hit:r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1&&hits.every(point=>point.inside)};
  });
  const reachable=async(locator,settle=false)=>{
   let box=await targetGeometry(locator);
   if(settle&&!(box.width>=44&&box.height>=44&&box.hit)){
    const initial=box;
    // Native strip scrolling changes the Overview width for 140ms. Wait on
    // the exact same corner-hit invariant, never click through an obstruction.
    await expect.poll(async()=>{box=await targetGeometry(locator);return box.width>=44&&box.height>=44&&box.hit},
     {timeout:1500,message:'Close control must have an unclipped 44px hit target: '+JSON.stringify(initial)}).toBe(true);
    (report.settledTargets??=[]).push({width,initial,settled:box});
   }
   assert.ok(box.width>=44&&box.height>=44&&box.hit,'Close control must have an unclipped 44px hit target');
  };
  const use=async locator=>{await reachable(locator,true);if(width<500)await locator.tap();else await locator.click()};
  const headerContained=async()=>{
   const sample=await panel.evaluate(el=>{
    const header=el.querySelector('.a-canvas-head').getBoundingClientRect(),content=el.querySelector('.a-canvas-content').getBoundingClientRect();
    return {headerBottom:header.bottom,contentTop:content.top,contained:[...el.querySelectorAll('.a-canvas-head>.a-open-documents,.a-canvas-head>.a-icon,.a-canvas-head>.a-canvas-dismiss')].every(button=>{
     const r=button.getBoundingClientRect();return r.top>=header.top&&r.bottom<=header.bottom+1&&r.left>=header.left&&r.right<=header.right+1;
    })};
   });
   assert.ok(sample.contained&&sample.contentTop>=sample.headerBottom-1,'Header actions must not overlap the reader');
   report.geometry.push({width,focused:(await state()).view.canvasFocused,...sample});
  };
  const preserved=async()=>{
   assert.equal((await artifactRows()).length,2);
   assert.deepEqual(await versions(),savedBefore,'Closing views cannot revise saved versions');
   const s=await state();
   assert.deepEqual(s.sessions.find(row=>row.id===s.selectedSessionId).messages,historyBefore);
   assert.deepEqual((await (await page.request.get(url+'/fixture')).json()).sent,fixtureBefore.sent);
  };
  const reopen=async title=>{
   await overview.click();
   const saved=panel.locator('.a-saved-artifact').filter({hasText:title});
   await saved.click();
   await expect(panel.getByRole('tab',{name:title,exact:true})).toHaveAttribute('aria-selected','true');
   await expect(panel.getByRole('heading',{name:title==='Beta report'?'Beta retained':'Alpha revised',exact:true})).toBeVisible();
  };
  await expect(dismiss).toContainText('Close Canvas');
  const label=await dismiss.evaluate(el=>{
   const span=el.querySelector('span'),range=document.createRange();range.selectNodeContents(span);
   const r=range.getBoundingClientRect(),b=el.getBoundingClientRect(),s=span.getBoundingClientRect();
   return {text:span.textContent,labelWidth:r.width,buttonWidth:b.width,
    contained:r.left>=s.left-1&&r.right<=s.right+1&&r.left>=b.left&&r.right<=b.right,
    clipped:span.scrollWidth>span.clientWidth+1};
  });
  assert.equal(label.text,'Close Canvas');assert.ok(label.contained&&!label.clipped);
  report.geometry.push({width,label});
  await headerContained();
  await reachable(dismiss);await reachable(closeTab('Beta report'));
  if(width===1500){
   // Deliberately remove the visible affordances; the same geometry oracle must
   // reject the pre-fix discovery failure, not merely accept existing actions.
   // Preserve layout/scroll position: this negative removes discoverability,
   // not width. display:none would also clamp the document strip's scroll.
   const hidden=await page.addStyleTag({content:'.a-canvas-tab-close,.a-canvas-dismiss{visibility:hidden!important}'});
   await assert.rejects(()=>reachable(closeTab('Beta report')),/unclipped 44px/);
   await assert.rejects(()=>reachable(dismiss),/unclipped 44px/);
   await hidden.evaluate(el=>el.remove());
   await reachable(dismiss);await reachable(closeTab('Beta report'));
   report.checks.push({name:'Missing visible close controls rejected by the same oracle',status:'passed'});
  }
  await use(closeTab('Beta report'));
  await expect(panel.getByRole('tab',{name:'Beta report',exact:true})).toHaveCount(0);
  await expect(panel.getByRole('tab',{name:'Alpha report',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(panel).toBeVisible();await preserved();
  await reopen('Beta report');
  // Native Tab navigation reaches the adjacent close button without a shortcut.
  const beta=panel.getByRole('tab',{name:'Beta report',exact:true});
  await beta.focus();await page.keyboard.press('Tab');
  await expect(closeTab('Beta report')).toBeFocused();await reachable(closeTab('Beta report'));
  await page.keyboard.press('Enter');
  await expect(beta).toHaveCount(0);await expect(panel.getByRole('tab',{name:'Alpha report',exact:true})).toBeFocused();
  await reopen('Beta report');
  // Closing another tab must not replace the selected document.
  await closeTab('Alpha report').scrollIntoViewIfNeeded();
  await use(closeTab('Alpha report'));
  await expect(beta).toHaveAttribute('aria-selected','true');assert.equal((await state()).canvas.id,second.id);
  await expect(panel.getByRole('tab',{name:'Alpha report',exact:true})).toHaveCount(0);
  await preserved();await reopen('Alpha report');
  // Keyboard traversal, not programmatic focus on the target close control.
  await panel.getByRole('tab',{name:'Alpha report',exact:true}).focus();
  for(let attempt=0;attempt<40&&!await dismiss.evaluate(el=>el===document.activeElement);attempt++)await page.keyboard.press('Tab');
  await expect(dismiss).toBeFocused();await reachable(dismiss);await capture('keyboard-close-panel-'+width);
  await page.keyboard.press('Enter');
  await expect(panel).toBeHidden();await expect(toggle).toBeFocused();
  const clientId=(await state()).client.id;
  await expect.poll(async()=>{
   const hiddenState=await (await page.request.get(url+'/api/state?clientId='+encodeURIComponent(clientId))).json();
   return hiddenState.canvas.open;
  },{message:'Panel hide must be accepted by the host, not only painted optimistically'}).toBe(false);
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
  assert.equal((await artifactRows()).filter(row=>row.tabOpen).length,2);await preserved();
  await toggle.press('Enter');await expect(panel).toBeVisible();
  await expect(panel.getByRole('heading',{name:'Alpha revised',exact:true})).toBeVisible();
  await use(dismiss);await expect(panel).toBeHidden();await toggle.click();
  // Full-frame and narrow dismissal share the retained visibility operation.
  await panel.getByRole('button',{name:'Focus canvas',exact:true}).click();
  await headerContained();await capture('focused-'+width);
  await use(dismiss);await expect(panel).toBeHidden();
  assert.equal((await state()).view.canvasFocused,false);
  await toggle.click();await expect(panel).toBeVisible();await preserved();
  // Last tab close leaves saved material discoverable in the still-open panel.
  await closeTab('Alpha report').scrollIntoViewIfNeeded();await use(closeTab('Alpha report'));
  await closeTab('Beta report').scrollIntoViewIfNeeded();await use(closeTab('Beta report'));
  await expect(panel).toBeVisible();await expect(panel.locator('.a-canvas-document-tabs [role=tab]')).toHaveCount(0);
  await preserved();await reopen('Alpha report');
  await capture('reopened-'+width);
  const accepted=await Promise.all(receipts);
  assert.ok(accepted.some(row=>row.command.action==='canvas.tabClose')&&accepted.some(row=>row.command.action==='canvas.visibility'));
  assert.deepEqual(accepted.filter(row=>row.status!==200||row.body?.accepted!==true),[],'Every close/dismiss must retain its actual accepted receipt');
  (report.closeReceipts??=[]).push({width,receipts:accepted});
  report.checks.push({width,status:'passed',closeOne:true,closePanel:true,keyboard:true,touch:width<500,versionsAndHistoryPreserved:true});
  await context.close();
 }
 assert.deepEqual(report.errors,[]);report.status='passed';
}catch(error){failure=error;report.status='failed';report.error=error.stack||String(error);if(page&&!page.isClosed())try{await capture('failure')}catch{}}
finally{
 await browser?.close();
 if(fixture.pid&&fixture.exitCode===null&&fixture.signalCode===null){const exited=once(fixture,'exit'),timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);fixture.kill('SIGTERM');try{await exited}finally{clearTimeout(timer)}}
 await writeFile(join(output,'fixture.stdout.log'),stdout);await writeFile(join(output,'fixture.stderr.log'),stderr);
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({test:'canvas-close',status:report.status,output,observe}));
}
if(failure)throw failure;
