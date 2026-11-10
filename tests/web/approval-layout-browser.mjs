import {spawn} from 'node:child_process';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Run after building this exact source, in an owned isolated test environment.
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('AMPLIFIER_TEST_ARTIFACTS must name an owned evidence directory');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/approval_layout_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;
const results=[];
fixture.stderr.on('data',chunk=>logs+=chunk);
const unwrap=raw=>raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
const columnMatches=({approvals,composer})=>Math.abs(approvals.x-composer.x)<=1&&Math.abs(approvals.width-composer.width)<=1;
const near=(a,b)=>Math.abs(a-b)<=0.5;
const sameGeometry=(a,b)=>typeof a==='number'&&typeof b==='number'?near(a,b)
 :a&&b&&typeof a==='object'&&typeof b==='object'
  ?Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(key=>sameGeometry(a[key],b[key])):a===b;
// Independent fixture expectations, not the product's fitPanels calculation.
// Equal approval/composer widths alone also accept a stale docked mobile nav.
const layoutMatches=(s,e)=>{
 const narrow=e.width<=900,right=box=>box.x+box.width;
 if(s.viewport.width!==e.width||s.viewport.height!==e.height||s.scheme!==e.scheme
  ||s.narrow!==narrow||s.canvasOpen!==!!e.canvasOpen||s.canvasOverlay
  ||!near(s.workspace.x,0)||!near(s.workspace.width,e.width))return false;
 if(narrow){
  if(s.nav.docked||!s.nav.hidden||!s.nav.inert||s.nav.rendered||!near(s.chat.x,0)||!near(s.chat.width,e.width))return false;
 }else if(!s.nav.docked||s.nav.hidden||s.nav.inert||!s.nav.rendered
  ||!near(s.nav.bounds.x,0)||!near(s.nav.bounds.width,e.navWidth)||!near(s.chat.x,right(s.nav.bounds)))return false;
 if(e.canvasOpen){
  if(!s.canvas.rendered||s.canvas.hidden||!near(s.canvas.bounds.x,right(s.chat))||!near(right(s.canvas.bounds),e.width))return false;
 }else if(s.canvas.rendered||!near(right(s.chat),e.width))return false;
 if(e.chatWidth!==undefined&&!near(s.chat.width,e.chatWidth))return false;
 const width=Math.min(760,s.chat.width-(s.chat.width<=520?28:48)),x=s.chat.x+(s.chat.width-width)/2;
 return columnMatches(s)&&[s.approvals,s.composer].every(box=>near(box.x,x)&&near(box.width,width))
  &&s.approvals.height>0&&s.approvals.height<=e.height*.3+1;
};
try{
 const ready=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Approval fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Approval fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{
   const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1600,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(8000);
 const errors=[],responses=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(request.method()==='POST'&&request.url().endsWith('/api/actions')){
   const body=unwrap(request.postDataJSON());if(body?.action==='approval.respond')responses.push(body);
  }
 });
 const act=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const patch=values=>act('view.update',{patch:values});
 const check=()=>page.request.get(ready.url+'/fixture/check').then(response=>{assert.equal(response.ok(),true);return response.json()});
 const card=page.locator('[data-part=approvals]');
 const geometry=()=>page.evaluate(()=>{
  const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
  const workspace=document.querySelector('[data-part=workspace]'),nav=document.querySelector('#workspace-navigation'),
   canvas=document.querySelector('#workspace-canvas'),card=document.querySelector('[data-part=approvals]'),c=card.getBoundingClientRect();
  return {viewport:{width:innerWidth,height:innerHeight},scheme:document.querySelector('#amp-one').dataset.themeScheme,
   narrow:workspace.dataset.narrow==='true',canvasOpen:workspace.dataset.canvasOpen==='true',canvasOverlay:workspace.dataset.canvasOverlay==='true',
   workspace:rect(workspace),nav:{docked:nav.dataset.docked==='true',hidden:nav.hidden,inert:nav.inert,rendered:!!nav.getClientRects().length,bounds:rect(nav)},
   canvas:{hidden:!canvas||canvas.hidden,rendered:!!canvas?.getClientRects().length,bounds:canvas?rect(canvas):null},
   approvals:rect(card),composer:rect(document.querySelector('[data-part=composer]')),chat:rect(document.querySelector('[data-part=conversation]')),
   approvalScrollTop:card.scrollTop,controls:[...card.querySelectorAll('button[data-action="approval.respond"]')].map(button=>{
    const r=button.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    return {id:button.closest('[data-approval-id]').dataset.approvalId,label:button.textContent.trim(),bounds:rect(button),focused:document.activeElement===button,
     reachable:r.width>0&&r.height>0&&r.left>=c.left+card.clientLeft&&r.right<=c.left+card.clientLeft+card.clientWidth+1
      &&r.top>=c.top+card.clientTop&&r.bottom<=c.top+card.clientTop+card.clientHeight+1
      &&r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&button.contains(hit)};
   })};
 });
 const settledLayout=async(expected,label)=>{
  let previous,current,stable=0;
  try{
   await expect.poll(async()=>{
    current=await geometry();
    stable=layoutMatches(current,expected)?(previous&&sameGeometry(previous,current)?stable+1:1):0;
    previous=current;return stable>=3;
   },{message:label+': expected responsive state and three stable samples',timeout:5000,intervals:[16,32,50]}).toBe(true);
  }catch(error){results.push({label,status:'failed',expected,lastSample:current});throw error}
  return current;
 };
 const capture=async(record,phase)=>{
  const evidence={file:record.label+'-'+phase+'.png',status:'incomplete',before:await settledLayout(record.expected,record.label+'-'+phase)};
  record.phases[phase]=evidence;
  await page.screenshot({path:join(output,evidence.file)});
  evidence.after=await geometry();
  // Never retry a mismatching capture into a pass: preserve both samples.
  assert.ok(layoutMatches(evidence.after,record.expected),record.label+': screenshot responsive state');
  assert.ok(sameGeometry(evidence.before,evidence.after),record.label+': geometry changed during '+phase+' screenshot');
  if(phase==='actions'){
   const controls=evidence.after.controls.filter(button=>button.id===ready.grant);
   assert.deepEqual(controls.map(button=>button.label),['Allow','Deny']);
   assert.ok(controls.every(button=>button.reachable)&&controls[1].focused,record.label+': captured actions fully reachable');
  }else assert.equal(evidence.after.approvalScrollTop,0,record.label+': captured top of content');
  evidence.status='passed';
 };
 const visibleControl=async button=>{
  await expect(button).toBeFocused();
  await expect.poll(()=>button.evaluate(element=>{
   const r=element.getBoundingClientRect(),card=element.closest('[data-part=approvals]'),c=card.getBoundingClientRect();
   const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
   return r.width>0&&r.height>0&&r.left>=c.left+card.clientLeft&&r.right<=c.left+card.clientLeft+card.clientWidth+1
    &&r.top>=c.top+card.clientTop&&r.bottom<=c.top+card.clientTop+card.clientHeight+1
    &&r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&element.contains(hit);
  })).toBe(true);
 };
 const probe=async(label,expected)=>{
  const record={label,status:'incomplete',expected,phases:{}};results.push(record);
  const bounds=await settledLayout(expected,label);
  assert.ok(bounds.approvals.x>=bounds.chat.x&&bounds.approvals.x+bounds.approvals.width<=bounds.chat.x+bounds.chat.width+1,label+': within conversation');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),label+': no page overflow');
  assert.ok(await card.evaluate(el=>el.scrollWidth<=el.clientWidth+1),label+': no approval overflow');
  for(const identity of ['layout-object','layout-string']){
   const details=page.locator(`[data-approval-id="${identity}"] pre`);
   await expect(details).toContainText('DETAILS END');
   assert.ok(await details.evaluate(el=>el.scrollWidth<=el.clientWidth+1),label+': long detail tokens reflow');
   assert.ok(await details.evaluate(el=>{el.scrollTop=el.scrollHeight;return el.scrollHeight>el.clientHeight&&el.scrollTop+el.clientHeight>=el.scrollHeight-1}),label+': all details remain scrollable');
  }
  // Focus and Tab must reveal both actions, not just create off-screen DOM nodes.
  // Never activate a permission response, including the synthetic one.
  for(const [index,identity] of ['layout-object','layout-string',ready.grant].entries()){
   const approval=page.locator(`[data-approval-id="${identity}"]`);
   const note=approval.getByRole('textbox',{name:'Note to Amplifier (optional)',exact:true});
   const allow=approval.getByRole('button',{name:'Allow',exact:true}),deny=approval.getByRole('button',{name:'Deny',exact:true});
   if(index===0)await note.focus();
   else{
    await page.keyboard.press('Tab');
    // Chromium includes the next request's scrollable details in tab order.
    // Permit that exact reading stop, never skip an arbitrary focused control.
    if(await approval.evaluate(el=>document.activeElement===el.querySelector('pre')))await page.keyboard.press('Tab');
   }
   // The optional note is an exact reading/editing stop before the actions.
   // Keep it empty and verify both directions without dispatching a decision.
   await expect(note).toBeFocused();await expect(note).toHaveValue('');
   await page.keyboard.press('Tab');await visibleControl(allow);
   await page.keyboard.press('Shift+Tab');await expect(note).toBeFocused();
   await page.keyboard.press('Tab');
   await visibleControl(allow);
   await page.keyboard.press('Tab');await visibleControl(deny);
   await page.keyboard.press('Shift+Tab');await visibleControl(allow);
   await page.keyboard.press('Tab');await visibleControl(deny);
  }
  assert.ok(await card.evaluate(el=>el.scrollTop>0),label+': lower requests reachable by keyboard');
  await capture(record,'actions');
  await card.evaluate(el=>{document.activeElement?.blur();el.scrollTop=0});
  await capture(record,'content');
  record.status='passed';
 };
 await page.goto(ready.url);await showFixtureConversation(page);await patch({navPinned:true});await expect(card).toBeVisible();
 await expect(card.locator('[data-approval-id]')).toHaveCount(3);
 const before=await check();
 await probe('desktop-light',{width:1600,height:1000,scheme:'light',navWidth:240});
 // Negative control restores the exact old width/margin behavior. A rendered
 // check that also accepts this state cannot protect the reported regression.
 const oldWidth=await page.addStyleTag({content:'#amp-one.a-chat-shell.a-approachable-shell .a-approvals{width:auto;max-width:none;margin-inline:18px;overflow-wrap:normal}'});
 try{
  await expect(card).toHaveCSS('max-width','none');
  await expect(card).toHaveCSS('margin-left','18px');
  const bounds=await geometry();assert.equal(columnMatches(bounds),false,'Old full-width behavior must be rejected');
  assert.ok(bounds.approvals.width>bounds.composer.width+100,'Negative control must actually stretch on desktop');
  results.push({label:'old-width-negative-control',status:'rejected',...bounds});
 }finally{await oldWidth.evaluate(el=>el.remove())}
 let navWidth=240;
 for(const scheme of ['light','dark']){
  await patch({scheme});
  for(const {width,height,suffix=''} of [{width:1600,height:1000},{width:390,height:844},{width:320,height:568},{width:900,height:480},
   // Repeat desktop -> mobile; never hide navigation manually to make it pass.
   {width:390,height:844,suffix:'-return'}]){
   await page.setViewportSize({width,height});await probe(`${scheme}-${width}x${height}${suffix}`,{width,height,scheme,navWidth});
  }
  // A narrow conversation inside a wide desktop must use container gutters.
  await page.setViewportSize({width:1600,height:1000});
  await settledLayout({width:1600,height:1000,scheme,navWidth},scheme+'-before-canvas');
  await act('canvas.show',{kind:'text',title:'Layout fixture',content:'Synthetic split-pane content'});
  await settledLayout({width:1600,height:1000,scheme,navWidth,canvasOpen:true},scheme+'-before-resize');
  const separator=page.getByRole('separator',{name:'Resize canvas'});
  await separator.focus();await page.keyboard.press('End');
  navWidth=216; // Canvas End preserves the minimum docked navigation width.
  await probe(scheme+'-split-360',{width:1600,height:1000,scheme,navWidth,canvasOpen:true,chatWidth:360});
  await act('canvas.close',{});
 }
 await page.reload();await expect(card.locator('[data-approval-id]')).toHaveCount(3);
 const after=await check();
 assert.deepEqual(after,before,'Resize, keyboard inspection and reload must preserve approvals/grants');
 assert.deepEqual(responses,[],'Layout inspection must never dispatch approval.respond');
 assert.deepEqual(errors,[],'No browser exceptions');
 console.log('PASS: approval reading column, long details, keyboard-visible actions, desktop/narrow/short/split layouts, light/dark, old-width negative control, pending state retained.');
}catch(error){failure=error;console.error(error.stack);console.error(logs)}
finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null&&fixture.pid){
  const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);
  try{await exited}finally{clearTimeout(timer)}
 }
 if(fixture.pid&&(fixture.exitCode!==0||fixture.signalCode))failure||=Error(`Fixture cleanup failed: ${fixture.exitCode} ${fixture.signalCode}`);
 await writeFile(join(output,'approval-layout-result.json'),JSON.stringify({status:failure?'failed':'passed',results,error:failure?.stack??null,fixtureLog:logs},null,2)+'\n');
}
if(failure)process.exitCode=1;
