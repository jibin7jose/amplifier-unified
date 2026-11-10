// Real built Peer preview + maintained synthetic host. Narrow header regression.
// MEASURE_ONLY retains old geometry without claiming acceptance. NEGATIVE restores
// the pre-fix header CSS/Close to prove this vertical/tooltip check discriminates.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const output=process.env.AMPLIFIER_PROOF_DIR;
assert.ok(output,'Choose a retained artifact directory');mkdirSync(output,{recursive:true});
const measurement=process.env.PEER_HEADER_MEASURE_ONLY==='1',negative=process.env.PEER_HEADER_NEGATIVE==='1';
assert.ok(!(measurement&&negative));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[
 fileURLToPath(new URL('../../tests/fixtures/peer_preview_ui_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))
],{stdio:['ignore','pipe','inherit']});
let browser,page;const samples=[],errors=[],commands=[];
const overlap=(a,b)=>a&&b&&Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1;
const inside=(a,b)=>a.left>=b.left-1&&a.right<=b.right+1&&a.top>=b.top-1&&a.bottom<=b.bottom+1;
function violations(g){
 const failures=[];
 for(const [name,box] of Object.entries({title:g.title,status:g.status,close:g.close}))if(!inside(box,g.header))failures.push(name+' escapes header');
 if(g.title.bottom>g.status.top+1)failures.push('title crowds Read only');
 if(overlap(g.title,g.close)||overlap(g.status,g.close))failures.push('Close overlaps identity');
 if(g.header.bottom>g.toolbar.top+1||g.status.bottom>g.toolbar.top+1)failures.push('header crowds toolbar');
 if(g.close.width<44||g.close.height<44||!g.closeCenterHits)failures.push('Close is not independently reachable at 44px');
 for(const tip of g.tooltips)for(const [name,box] of Object.entries({title:g.title,status:g.status,close:g.close,toolbar:g.toolbar,snapshot:g.snapshot}))if(overlap(tip,box))failures.push('tooltip obscures '+name);
 if(g.panelOverflow)failures.push('panel horizontal overflow');
 return failures;
}
try{
 const target=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout')),30000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n')){try{const r=JSON.parse(line);if(r.url){clearTimeout(timer);resolve(r.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:320,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-preview'}});
 page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions')commands.push(r.postDataJSON().action)});
 await page.goto(target);await showFixtureConversation(page,{navPinned:false});
 const facts=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
 const summary=page.locator('.a-peer-run-summary');await expect(summary).toHaveAttribute('aria-expanded','false');await summary.press('Enter');
 const source=page.locator(`[data-message-id="${facts.originals[1]}"]`).getByRole('button',{name:/^Preview source chat: Accessibility review/});
 const pane=page.getByRole('log',{name:'Conversation messages',exact:true}),composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const paneNode=await pane.elementHandle(),composerNode=await composer.elementHandle();
 const readState=()=>page.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerHeaderDocument}});
 await page.evaluate(()=>{window.peerHeaderDocument=crypto.randomUUID()});const before=await readState();
 for(const width of [320,390]){
  await page.setViewportSize({width,height:900});await source.scrollIntoViewIfNeeded();
  const top=await pane.evaluate(n=>n.scrollTop);
  await source.click();const panel=page.getByRole('dialog',{name:'Read-only source chat preview',exact:true});
  await expect(panel).toBeVisible();await expect(panel.locator('.a-canvas-head strong')).toHaveText('Accessibility review — keyboard, touch and narrow screen behavior');
  await expect(panel.getByRole('button',{name:'Open chat',exact:true})).toBeEnabled();
  await expect(panel.locator('[data-preview-message-id]')).toHaveCount(1);
  const close=panel.getByRole('button',{name:'Close source preview',exact:true});
  if(!measurement&&!negative)await expect(close).toHaveText('Close');
  if(negative){
   // Scoped DOM/CSS mutation only: restore the original header block while
   // preserving the actual built component, text, source proof and fixture.
   await page.addStyleTag({content:`#amp-one.a-chat-shell .a-peer-preview-panel .a-canvas-head{display:flex;height:52px;min-height:52px;flex-wrap:wrap;align-items:center;padding:12px 16px}#amp-one.a-chat-shell .a-peer-preview-panel .a-canvas-head>div{flex:0 1 auto;min-width:0}#amp-one.a-chat-shell .a-peer-preview-panel .a-canvas-head strong,#amp-one.a-chat-shell .a-peer-preview-panel .a-canvas-head small{display:inline}#amp-one.a-chat-shell .a-peer-preview-panel .a-canvas-head>button{width:44px;min-width:44px;height:44px;min-height:44px;padding:8px;flex:0 1 auto}`});
   await close.evaluate(n=>{n.removeAttribute('data-tooltip');n.classList.add('a-icon');for(const child of [...n.childNodes])if(child.nodeType===Node.TEXT_NODE||child.nodeName==='SPAN')child.remove()});
  }
  // Neutral, keyboard focus and pointer hover are distinct retained states.
  for(const state of ['neutral','focus','hover']){
   if(state==='neutral')await close.evaluate(n=>n.blur());
   if(state==='focus')await close.focus();
   if(state==='hover'){await close.evaluate(n=>n.blur());await close.hover()}
   await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
   const geometry=await panel.evaluate(panel=>{
    const header=panel.querySelector('.a-canvas-head'),title=header.querySelector('strong'),status=header.querySelector('small'),close=header.querySelector('button'),toolbar=panel.querySelector('.a-peer-preview-toolbar'),snapshot=panel.querySelector('.a-peer-snapshot-status');
    const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
    const textRects=node=>{const range=document.createRange();range.selectNodeContents(node);return [...range.getClientRects()].map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}))};
    const style=node=>{const s=getComputedStyle(node);return Object.fromEntries(['display','height','minHeight','width','minWidth','flex','flexWrap','alignItems','padding','fontSize','lineHeight','overflow','whiteSpace'].map(k=>[k,s[k]]))};
    const box=rect(close),hit=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
    return {header:rect(header),identity:rect(header.querySelector('div')),title:rect(title),titleLines:textRects(title),status:rect(status),statusLines:textRects(status),close:box,toolbar:rect(toolbar),snapshot:rect(snapshot),computed:{header:style(header),identity:style(header.querySelector('div')),title:style(title),status:style(status),close:style(close)},tooltips:[...document.querySelectorAll('[role="tooltip"]')].filter(n=>n.getClientRects().length).map(rect),closeCenterHits:hit===close||close.contains(hit),closeFocused:document.activeElement===close,panelOverflow:panel.scrollWidth>panel.clientWidth};
   });
   const issues=violations(geometry);
   if(state==='focus'&&!geometry.closeFocused)issues.push('Close did not retain keyboard focus');
   const row={width,state,geometry,violations:issues};samples.push(row);
   await page.screenshot({path:join(output,`peer-header-${width}-${state}.png`)});
   if(negative&&state==='focus'&&samples.some(r=>r.violations.some(v=>/escapes header|crowds/.test(v)))){
    // The predecessor's confirmed collision blocks real hover. Preserve the
    // discriminating neutral/focus geometry and fail before that interaction;
    // never force it or count later states/continuity assertions as reached.
    writeFileSync(join(output,'header-geometry.json'),JSON.stringify({
     mode:'negative',acceptance:'failed',samples,commands,
     stoppedAt:{width,state,reason:'confirmed vertical collision before hover'},
     postLoopAssertions:'not_run'
    },null,2));
    throw Error('PEER_HEADER_NEGATIVE_CONFIRMED: '+JSON.stringify(samples.map(r=>({width:r.width,state:r.state,violations:r.violations}))));
   }
  }
  assert.deepEqual(await readState(),before);assert.equal(await composerNode.evaluate(n=>n.isConnected&&!!n.closest('[inert]')),true);
  await close.press('Escape');await expect(panel).toHaveCount(0);await expect(source).toBeFocused();
  assert.deepEqual(await readState(),before);assert.equal(await pane.evaluate((n,old)=>n===old,paneNode),true);assert.equal(await composer.evaluate((n,old)=>n===old,composerNode),true);
  assert.ok(Math.abs(await pane.evaluate(n=>n.scrollTop)-top)<2);assert.equal(await composer.evaluate(n=>!!n.closest('[inert]')),false);
 }
 const final=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();assert.deepEqual(final.effects,[]);assert.deepEqual(final.subprocesses,[]);assert.deepEqual(errors,[]);
 assert.ok(!commands.some(n=>['session.select','conversation.send','worker.spawn','session.warm','approval.respond'].includes(n)));
 const failures=samples.filter(row=>row.violations.length);
 writeFileSync(join(output,'header-geometry.json'),JSON.stringify({mode:measurement?'measurement':negative?'negative':'positive',acceptance:measurement?'not_checked':failures.length?'failed':'passed',samples,commands},null,2));
 if(negative){assert.ok(failures.length,'Negative incorrectly passed');assert.ok(failures.some(r=>r.violations.some(v=>/escapes header|crowds/.test(v))),'Expected vertical collision not detected');throw Error('PEER_HEADER_NEGATIVE_CONFIRMED: '+JSON.stringify(failures.map(r=>({width:r.width,state:r.state,violations:r.violations}))))}
 if(!measurement)assert.deepEqual(failures,[],'Peer header collision or unreachable Close');
 console.log(JSON.stringify({mode:measurement?'measurement':'positive',violatingStates:failures.length,states:samples.length,noNavigation:true,noRuntime:true,parentContinuity:true}));
}catch(error){
 writeFileSync(join(output,'header-failure.json'),JSON.stringify({error:String(error),samples},null,2));throw error;
}finally{
 await browser?.close();
 if(fixture.exitCode===null){fixture.kill();await new Promise(done=>fixture.once('exit',done));}
}
