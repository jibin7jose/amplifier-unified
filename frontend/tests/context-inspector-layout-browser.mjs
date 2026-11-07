// Actual built client + production inspection reads of synthetic capture.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/context-layout';
await mkdir(output,{recursive:true});
const report={checks:[],geometry:[],errors:[],screenshots:[],baseline:process.env.CONTEXT_LAYOUT_BASELINE==='1'};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/context_inspector_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('error',e=>{clearTimeout(timer);reject(e)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exit ${code}`))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(8000);
 page.on('pageerror',e=>report.errors.push(e.message));
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 const action=async(action,args)=>{const r=await page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);assert.equal(r.accepted,true);return r};
 const fixtureState=await (await page.request.get(url+'/fixture/context')).json();
 await page.request.post(url+'/fixture/context',{data:{scenario:'long-list'}});
 await action('session.select',{id:fixtureState.sessionId});
 await action('view.update',{patch:{workSurface:'chat',panel:null,canvasFocused:true}});
 await action('canvas.show',{kind:'markdown',title:'Keep this document',content:'# Original document\nPreserve other Canvas readers.'});
 await action('canvas.show',{kind:'context-inspector',sessionId:fixtureState.sessionId});
 const panel=page.locator('.a-context-inspector');
 const capture=async name=>{await page.screenshot({path:join(output,name+'.png')});report.screenshots.push(name+'.png')};
 const geometry=async name=>{const row=await panel.evaluate(el=>{
  const rect=n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return {top:r.top,bottom:r.bottom,width:r.width,height:r.height,scrollTop:n.scrollTop,scrollHeight:n.scrollHeight,clientHeight:n.clientHeight,scrollWidth:n.scrollWidth,clientWidth:n.clientWidth,overflowY:s.overflowY}};
  return {panel:rect(el),outer:rect(el.closest('.a-resource-renderer')),nav:rect(el.querySelector('.a-context-nav')),reader:rect(el.querySelector('.a-context-reader')),pre:el.querySelector('pre')?rect(el.querySelector('pre')):null};
 });report.geometry.push({name,...row});return row};
 await panel.locator('.a-context-category').filter({hasText:'Instructions & files'}).click();
 await panel.locator('.a-context-item').filter({hasText:'source-00.md'}).click();
 await expect(panel.getByLabel('Captured text')).toContainText('End of source.');
 await geometry('short-reader-start');await capture('short-reader-start');
 if(report.baseline){
  await panel.locator('.a-context-item').filter({hasText:'source-59.md'}).scrollIntoViewIfNeeded();
  await geometry('long-list-end');await capture('long-list-end');
  await page.setViewportSize({width:390,height:844});await capture('mobile-header');
 }else{
  const list=panel.getByRole('region',{name:'Captured items',exact:true}),reader=panel.getByRole('region',{name:'Captured item reader',exact:true});
  const scrollTop=locator=>locator.evaluate(el=>el.scrollTop);
  const end=locator=>locator.evaluate(el=>Math.max(0,el.scrollHeight-el.clientHeight));
  const keyEnd=async locator=>{await locator.focus();await locator.press('Control+End');await expect.poll(()=>scrollTop(locator)).toBeGreaterThan((await end(locator))-2)};
  const keyStart=async locator=>{await locator.focus();await locator.press('Control+Home');await expect.poll(()=>scrollTop(locator)).toBeLessThan(2)};
  const wheel=async(locator,delta)=>{const box=await locator.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,delta)};
  const checkView=async name=>{
   const g=await geometry(name);
   assert.ok(g.outer.scrollHeight<=g.outer.clientHeight+1,'outer renderer must not grow with list');
   assert.ok(g.panel.scrollWidth<=g.panel.clientWidth+1,'no horizontal overflow');
   assert.ok(g.reader.bottom<=g.panel.bottom+1,'reader bounded by pane');
   assert.ok(g.pre.height<=g.pre.scrollHeight+1&&g.pre.overflowY==='visible','no inner pre scroller');
   const chrome=await page.locator('#workspace-canvas .a-canvas-head').evaluate(el=>{
    const r=el.getBoundingClientRect(),tab=el.querySelector('[role=tab][aria-selected=true]'),t=tab.getBoundingClientRect(),strip=tab.closest('.a-canvas-document-tabs').getBoundingClientRect();
    return {height:r.height,overflow:el.scrollWidth-el.clientWidth,tabWidth:t.width,visibleWidth:Math.min(t.right,strip.right)-Math.max(t.left,strip.left)};
   });
   assert.ok(chrome.height<=56&&chrome.overflow<=1,JSON.stringify(chrome));assert.ok(chrome.visibleWidth>=85,JSON.stringify(chrome));
   report.geometry.push({name:name+'-chrome',...chrome});
  };
  await checkView('desktop-short');
  assert.equal(await panel.locator('.a-context-pagination').count(),0,'no dead one-page pagination');
  assert.equal(await reader.evaluate(el=>el.scrollHeight<=el.clientHeight+1),true,'short reader fits');
  const top=(await reader.boundingBox()).y;
  await wheel(list,650);await expect.poll(()=>scrollTop(list)).toBeGreaterThan(100);
  await capture('desktop-list-middle');await geometry('desktop-list-middle');
  await keyEnd(list);await capture('desktop-list-end');await geometry('desktop-list-end');
  assert.equal((await reader.boundingBox()).y,top);await expect(panel.getByLabel('Captured text')).toContainText('Short captured source 00.');
  await wheel(list,500);assert.equal(await scrollTop(reader),0);await keyStart(list);
  report.checks.push('60-item list owns wheel/keyboard start-middle-end without moving short reader or outer pane');
  await panel.locator('.a-context-item').filter({hasText:'source-01.md'}).click();
  await expect(panel.getByLabel('Captured text')).toContainText('Long reader line');
  await wheel(reader,600);await expect.poll(()=>scrollTop(reader)).toBeGreaterThan(100);
  await capture('desktop-reader-middle');await geometry('desktop-reader-middle');
  await keyEnd(reader);await capture('desktop-reader-end');await geometry('desktop-reader-end');
  await panel.getByRole('button',{name:'Next page',exact:true}).click();
  await expect(panel.locator('.a-context-pagination')).toContainText('6001');await expect.poll(()=>scrollTop(reader)).toBeLessThan(2);
  report.checks.push('reader owns scroll, bottom pagination reachable, next page resets only reader scroll');
  const call=await panel.getAttribute('data-selected-call');
  await panel.getByRole('searchbox').fill('source-01');
  await page.getByRole('button',{name:'Exit canvas focus',exact:true}).click();
  const divider=page.getByRole('separator',{name:'Resize canvas'});
  await divider.focus();await divider.press('Home');await expect.poll(()=>panel.evaluate(el=>el.clientWidth)).toBeLessThan(400);
  await checkView('narrow-native-divider');
  await divider.press('End');await expect.poll(()=>panel.evaluate(el=>el.clientWidth)).toBeGreaterThan(650);
  await checkView('medium-native-divider');await capture('medium-pane');
  await page.getByRole('button',{name:'Focus canvas',exact:true}).click();
  for(const width of [390,320]){
   await page.setViewportSize({width,height:844});
   await expect(panel.locator('.a-context-nav')).not.toBeVisible();
   await expect(panel.locator('.a-context-pagination')).toContainText('6001');
   await keyStart(reader);await checkView('mobile-'+width+'-start');await capture('mobile-'+width+'-start');
   await wheel(reader,450);await expect.poll(()=>scrollTop(reader)).toBeGreaterThan(100);await capture('mobile-'+width+'-middle');
   await keyEnd(reader);await capture('mobile-'+width+'-end');await checkView('mobile-'+width+'-end');
   await expect(panel.getByRole('button',{name:'Next page',exact:true})).toBeInViewport();
   const range=panel.locator('.a-context-pagination small');
   assert.ok(await range.evaluate(el=>el.getBoundingClientRect().height<=25),'mobile page range stays one line');
  }
  await panel.getByRole('button',{name:'Back to items'}).click();
  await expect(panel.locator('.a-context-item').filter({hasText:'source-01.md'})).toBeFocused();
  await expect(panel.getByRole('searchbox')).toHaveValue('source-01');
  await panel.getByRole('searchbox').fill('');await keyEnd(list);await capture('mobile-list-end');
  await keyStart(list);await panel.locator('.a-context-item').filter({hasText:'source-01.md'}).click();await keyEnd(reader);
  await panel.getByRole('button',{name:'Next page',exact:true}).click();
  report.checks.push('320/390 mobile has one active content scroll, explicit Back, reachable pagination and retained search');
  // The exact selection/page survives an unrelated document visit.
  await page.getByRole('button',{name:/Open documents, /}).click();
  await page.getByRole('dialog',{name:'Open documents',exact:true}).getByRole('button',{name:'Keep this document markdown',exact:false}).click();
  await expect(page.locator('.a-canvas-workspace')).toContainText('Preserve other Canvas readers.');
  await page.getByRole('button',{name:/Open documents, /}).click();
  await page.getByRole('dialog',{name:'Open documents',exact:true}).getByRole('button',{name:/Context inspector context-inspector/}).click();
  await expect(panel.locator('.a-context-pagination')).toContainText('6001');
  await expect(panel).toHaveAttribute('data-selected-call',call);
  await expect(panel.locator('.a-context-reader h3')).toContainText('source-01.md');
  await capture('mobile-returned-inspector');
  await panel.getByRole('button',{name:'Back to items'}).click();
  await expect(panel.locator('.a-context-item').filter({hasText:'source-01.md'})).toBeFocused();
  await panel.locator('.a-context-item').filter({hasText:'source-01.md'}).click();
  for(let i=0;i<5;i++){
   await keyEnd(reader);
   const next=panel.getByRole('button',{name:'Next page',exact:true});
   if(await next.isDisabled())break;
   await next.click();
  }
  await keyEnd(reader);await expect(panel.getByRole('button',{name:'Next page',exact:true})).toBeDisabled();
  await capture('mobile-final-page');
  await panel.getByRole('button',{name:'Canvas details',exact:true}).click();
  await expect(page.getByRole('button',{name:'Close canvas details',exact:true})).toBeFocused();
  await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Canvas details',exact:true})).toBeFocused();
  await expect(page.locator('#workspace-canvas .a-canvas-artifact-toolbar')).not.toBeVisible();
  await expect(panel.locator('.a-context-pagination')).toContainText('18001');
  report.checks.push('Open documents stays usable for non-inspector navigation; selected request/item/page survives');
  // All data is still from production capture; only scenario is synthetic.
  await page.request.post(url+'/fixture/context',{data:{scenario:'long-list-unknown'}});
  await panel.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(panel.locator('.a-context-window')).toContainText('Model window unknown');
  await expect(panel.locator('.a-context-capacity-track')).toHaveCount(0);
  await capture('mobile-unknown-window');
  await expect.poll(()=>scrollTop(list)).toBeLessThan(2);
  await panel.locator('.a-context-evidence summary').click();
  await expect(panel.locator('.a-context-evidence')).toContainText('No matching preflight');
  await capture('mobile-limit-evidence');
  await panel.locator('.a-context-evidence summary').click();
  await page.setViewportSize({width:1440,height:1000});
  await panel.locator('.a-context-category').filter({hasText:'Instructions & files'}).click();
  await panel.locator('.a-context-item').filter({hasText:'source-00.md'}).click();
  await capture('desktop-unknown-window');
  const uiState=await page.evaluate(()=>window.amplifier.getShellState());
  const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
  const prepared=await action('shell.changes.prepare',{clientId,expectedRevision:uiState.revision,composition:{...uiState.effectiveComposition,presentation:{...uiState.effectiveComposition.presentation,scheme:'dark'}}});
  await action('shell.changes.apply',{clientId,expectedRevision:uiState.revision,changeId:prepared.result.id});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme','dark');await capture('desktop-dark-unknown');
  await page.request.post(url+'/fixture/context',{data:{scenario:'known-window-no-usage'}});
  await panel.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(panel.locator('.a-context-window')).toContainText('Model window 8,000');
  await expect(panel.locator('.a-context-window')).toContainText('Input usage unavailable');
  await expect(panel.locator('.a-context-capacity-track')).toHaveCount(0);
  report.checks.push('unknown denominator and known-window-without-usage never invent percentages; evidence on demand, dark theme');
  const after=await (await page.request.get(url+'/fixture/context')).json();assert.equal(after.providerCalls,4);report.fixtureProviderCalls=after.providerCalls;
 }
 assert.deepEqual(report.errors,[]);
}catch(error){report.failure=error.stack;if(page)await page.screenshot({path:join(output,'failure.png')}).catch(()=>{});throw error}
finally{
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){const exit=once(fixture,'exit');fixture.kill('SIGTERM');let timer;try{await Promise.race([exit,new Promise((_,reject)=>{timer=setTimeout(()=>{fixture.kill('SIGKILL');reject(Error('Fixture cleanup timeout'))},5000)})])}finally{clearTimeout(timer)}}
}